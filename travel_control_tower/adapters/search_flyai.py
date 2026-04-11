from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import time
from datetime import date, timedelta
from pathlib import Path

from ..runtime_config import load_runtime_config
from .base import HotelCandidate, POICandidate, TransportCandidate


DEFAULT_FLYAI_PATH = Path.home() / "AppData" / "Roaming" / "npm" / "flyai.cmd"
DEFAULT_CACHE_DIR = Path.home() / ".codex" / "travel-control-tower" / "flyai-cache"
DEFAULT_CACHE_TTL_SECONDS = 24 * 60 * 60
CACHE_VERSION = "v2"


def _display_poi_category(value: object) -> str:
    text = str(value or "").strip()
    if not text or text.lower() in {"none", "null", "unknown"}:
        return ""
    return text


def _display_poi_level(value: object) -> str:
    text = str(value or "").strip()
    if not text or text.lower() in {"none", "null", "unknown"}:
        return ""
    return text


def _display_free_status(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = text.lower()
    if normalized in {"unknown", "none", "null"}:
        return ""
    if normalized == "free":
        return "免费"
    if normalized == "not_free":
        return "需购票"
    return text


class FlyAISearchAdapter:
    provider_name = "flyai"

    def __init__(
        self,
        command: str | None = None,
        cache_dir: Path | None = None,
        cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
    ) -> None:
        runtime = load_runtime_config()
        self.command = command or runtime.flyai_cmd or self._discover_command()
        self.last_error = ""
        self.last_warning = ""
        self.last_source = ""
        self.cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self.cache_ttl_seconds = cache_ttl_seconds
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def is_available(self) -> bool:
        return bool(self.command)

    def search_hotels(
        self,
        destination: str,
        check_in: str,
        check_out: str,
        keyword: str = "",
        max_price: int = 1000,
        sort: str = "price_asc",
        hotel_types: str = "",
        hotel_stars: str = "",
        bed_types: str = "",
    ) -> list[HotelCandidate]:
        if not self.command:
            return []

        args = [
            "search-hotel",
            "--dest-name",
            destination,
            "--check-in-date",
            check_in,
            "--check-out-date",
            check_out,
            "--sort",
            sort,
            "--max-price",
            str(max_price),
        ]
        if keyword:
            args.extend(["--key-words", keyword])
        if hotel_types:
            args.extend(["--hotel-types", hotel_types])
        if hotel_stars:
            args.extend(["--hotel-stars", hotel_stars])
        if bed_types:
            args.extend(["--hotel-bed-types", bed_types])
        payload = self._run_json(args)
        items = ((payload.get("data") or {}).get("itemList") or [])
        dest_tokens = _city_tokens(destination)
        scored_items: list[tuple[int, int, dict]] = []
        for item in items:
            destination_score = _hotel_destination_score(item, dest_tokens)
            keyword_score = _hotel_keyword_score(str(item.get("name", "")).strip(), keyword) if keyword else 0
            quality_score = _hotel_quality_score(item)
            scored_items.append((destination_score, destination_score * 100 + keyword_score + quality_score, item))

        if any(destination_score > 0 for destination_score, _, _ in scored_items):
            scored_items = [pair for pair in scored_items if pair[0] > 0]

        scored_items.sort(key=lambda pair: (-pair[1], self._safe_float(pair[2].get("price"))))

        candidates: list[HotelCandidate] = []
        for _, _, item in scored_items:
            area = (
                str(item.get("interestsPoi", "")).strip()
                or str(item.get("cityName", "")).strip()
                or str(item.get("address", "")).strip()
            )
            candidates.append(
                HotelCandidate(
                    name=str(item.get("name", "")).strip(),
                    nightly_price=self._safe_float(item.get("price")),
                    area=area,
                    notes=self._hotel_notes(item),
                    booking_url=str(item.get("detailUrl", "")).strip(),
                    provider=self.provider_name,
                )
            )
        return candidates

    def keyword_search_hotels(self, query: str) -> list[HotelCandidate]:
        if not self.command:
            return []

        payload = self._run_json(["keyword-search", "--query", query])
        items = ((payload.get("data") or {}).get("itemList") or [])
        destination_hint = _extract_destination_hint(query)
        dest_tokens = _city_tokens(destination_hint) if destination_hint else []

        scored_items: list[tuple[int, dict]] = []
        for item in items:
            info = item.get("info") or {}
            score = _keyword_hotel_destination_score(info, dest_tokens) if dest_tokens else 0
            scored_items.append((score, info))

        if dest_tokens and any(score > 0 for score, _ in scored_items):
            scored_items = [pair for pair in scored_items if pair[0] > 0]
        scored_items.sort(key=lambda pair: -pair[0])

        candidates: list[HotelCandidate] = []
        for score, info in scored_items:
            if not _looks_like_hotel_product(info):
                continue
            area = (
                str(info.get("areaName", "")).strip()
                or str(info.get("cityName", "")).strip()
            )
            candidates.append(
                HotelCandidate(
                    name=str(info.get("title", "")).strip(),
                    nightly_price=self._safe_float(info.get("price")),
                    area=area,
                    notes=self._keyword_hotel_notes(info),
                    booking_url=str(info.get("jumpUrl", "")).strip(),
                    provider=self.provider_name,
                )
            )
        return candidates

    def search_transport(
        self,
        departure_city: str,
        destination: str,
        start_date: str,
        end_date: str,
    ) -> list[TransportCandidate]:
        if not self.command:
            return []

        payload = self._run_json(
            [
                "search-flight",
                "--origin",
                departure_city,
                "--destination",
                destination,
                "--dep-date",
                start_date,
                "--back-date",
                end_date,
                "--journey-type",
                "1",
                "--seat-class-name",
                "economy",
                "--sort-type",
                "3",
            ]
        )
        items = ((payload.get("data") or {}).get("itemList") or [])

        candidates: list[TransportCandidate] = []
        for item in items:
            journeys = item.get("journeys") or []
            if len(journeys) < 2:
                continue
            outbound = (journeys[0].get("segments") or [{}])[0]
            inbound = (journeys[1].get("segments") or [{}])[0]
            if not self._flight_matches(outbound, inbound, departure_city, destination):
                continue
            candidates.append(
                TransportCandidate(
                    label=self._transport_label(outbound, inbound),
                    category="往返机票",
                    total_price=self._safe_float(item.get("ticketPrice")),
                    depart_at=str(outbound.get("depDateTime", "")).strip(),
                    arrive_at=str(inbound.get("arrDateTime", "")).strip(),
                    outbound_arrive_at=str(outbound.get("arrDateTime", "")).strip(),
                    return_depart_at=str(inbound.get("depDateTime", "")).strip(),
                    trip_start_date=start_date,
                    trip_end_date=end_date,
                    booking_url=str(item.get("jumpUrl", "")).strip(),
                    provider=self.provider_name,
                )
            )
        return candidates

    def search_trains(
        self,
        departure_city: str,
        destination: str,
        start_date: str,
        end_date: str,
        outbound_hour_start: int = 6,
        outbound_hour_end: int = 12,
        return_hour_start: int = 16,
        return_hour_end: int = 22,
        max_options: int = 3,
    ) -> list[TransportCandidate]:
        if not self.command:
            return []

        outbound = self._search_single_train_leg(
            origin=departure_city,
            destination=destination,
            travel_date=start_date,
            hour_start=outbound_hour_start,
            hour_end=outbound_hour_end,
            max_options=max_options,
        )
        inbound = self._search_single_train_leg(
            origin=destination,
            destination=departure_city,
            travel_date=end_date,
            hour_start=return_hour_start,
            hour_end=return_hour_end,
            max_options=max_options,
        )
        if not outbound or not inbound:
            return []

        candidates: list[TransportCandidate] = []
        for out_item in outbound[:max_options]:
            for back_item in inbound[:max_options]:
                total_price = out_item["price"] + back_item["price"]
                label = (
                    f"{out_item['train_no']} 去程 / {back_item['train_no']} 回程"
                    f"（{out_item['dep_at'][11:16]} - {back_item['arr_at'][11:16]}）"
                )
                candidates.append(
                    TransportCandidate(
                        label=label,
                        category="往返高铁",
                        total_price=round(total_price, 2),
                        depart_at=out_item["dep_at"],
                        arrive_at=back_item["arr_at"],
                        outbound_arrive_at=out_item["arr_at"],
                        return_depart_at=back_item["dep_at"],
                        trip_start_date=start_date,
                        trip_end_date=end_date,
                        booking_url=out_item["jump_url"] or back_item["jump_url"],
                        provider=self.provider_name,
                    )
                )
        candidates.sort(key=lambda item: item.total_price or 0)
        return candidates[:max_options]

    def scan_transport_windows(
        self,
        departure_city: str,
        destination: str,
        window_start: str,
        window_end: str,
        trip_days: int,
        max_samples: int = 8,
    ) -> list[TransportCandidate]:
        if not self.command:
            return []

        start = date.fromisoformat(window_start)
        end = date.fromisoformat(window_end)
        trip_days = max(trip_days, 2)
        if end < start:
            return []

        sample_dates = self._build_sample_dates(start, end, trip_days, max_samples=max_samples)
        candidates: list[TransportCandidate] = []
        for sample_start in sample_dates:
            sample_end = sample_start + timedelta(days=trip_days - 1)
            if sample_end > end:
                continue
            try:
                results = self.search_transport(
                    departure_city=departure_city,
                    destination=destination,
                    start_date=sample_start.isoformat(),
                    end_date=sample_end.isoformat(),
                )
            except Exception:
                continue
            if results:
                candidate = results[0]
                candidate.trip_start_date = sample_start.isoformat()
                candidate.trip_end_date = sample_end.isoformat()
                candidates.append(candidate)
        candidates.sort(key=lambda item: item.total_price or 0)
        return candidates

    def search_pois(
        self,
        city_name: str,
        keyword: str = "",
        max_items: int = 8,
    ) -> list[POICandidate]:
        if not self.command:
            return []

        args = ["search-poi", "--city-name", city_name]
        if keyword:
            args.extend(["--keyword", keyword])

        payload = self._run_json(args)
        items = ((payload.get("data") or {}).get("itemList") or [])
        dest_tokens = _city_tokens(city_name)

        scored_items: list[tuple[int, dict]] = []
        for item in items[: max_items * 3]:
            score = _poi_destination_score(item, dest_tokens)
            scored_items.append((score, item))
        if any(score > 0 for score, _ in scored_items):
            scored_items = [pair for pair in scored_items if pair[0] > 0]
        scored_items.sort(
            key=lambda pair: (
                -pair[0],
                str(pair[1].get("poiLevel", "")).strip() == "",
                str(pair[1].get("name", "")).strip(),
            )
        )

        candidates: list[POICandidate] = []
        for score, item in scored_items:
            candidates.append(
                POICandidate(
                    name=str(item.get("name", "")).strip(),
                    city_name=city_name,
                    category=str(item.get("category", "")).strip(),
                    poi_level=str(item.get("poiLevel", "")).strip(),
                    free_status=str(item.get("freePoiStatus", "")).strip(),
                    address=str(item.get("address", "")).strip(),
                    notes=self._poi_notes(item),
                    booking_url=str(item.get("jumpUrl", "")).strip(),
                    provider=self.provider_name,
                )
            )
        return candidates[:max_items]

    def _run_json(self, args: list[str]) -> dict:
        cache_key = self._cache_key(args)
        cached_payload = self._load_cached_payload(cache_key)
        if cached_payload is not None:
            self.last_error = ""
            self.last_warning = ""
            self.last_source = "cache"
            return cached_payload

        completed = subprocess.run(
            [self.command, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=45,
        )
        raw = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()

        if "Trial limit reached" in raw or "Trial limit reached" in stderr:
            self.last_error = "FlyAI 当前试用额度已用尽，请改用正式 API key。"
            raise RuntimeError(self.last_error)

        if not raw:
            self.last_error = stderr or "FlyAI 没有返回可解析的结果。"
            raise RuntimeError(self.last_error)

        try:
            payload = json.loads(self._extract_json(raw))
        except Exception as exc:  # pragma: no cover
            self.last_error = f"FlyAI 输出无法解析：{exc}"
            raise RuntimeError(self.last_error) from exc

        error = payload.get("error")
        if isinstance(error, dict):
            message = str(error.get("message", "")).strip()
            if message:
                self.last_error = message
                raise RuntimeError(message)

        status = payload.get("status")
        message = str(payload.get("message", "")).strip().lower()
        if completed.returncode != 0 and status not in (0, "0") and message != "success":
            self.last_error = stderr or "FlyAI 调用失败。"
            raise RuntimeError(self.last_error)

        self.last_error = ""
        self.last_warning = ""
        self.last_source = "live"
        self._write_cached_payload(cache_key, payload)
        return payload

    @staticmethod
    def _extract_json(raw: str) -> str:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end < 0 or end <= start:
            raise RuntimeError("FlyAI 输出里没有找到 JSON 结果。")
        return raw[start : end + 1]

    @staticmethod
    def _safe_float(value) -> float:
        if value is None:
            return 0.0
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip()
        if not text:
            return 0.0
        normalized = (
            text.replace(",", "")
            .replace("¥", "")
            .replace("￥", "")
            .replace("元", "")
            .replace("起", "")
            .replace("约", "")
            .strip()
        )
        match = re.search(r"-?\d+(?:\.\d+)?", normalized)
        if match:
            try:
                return float(match.group(0))
            except ValueError:
                return 0.0
        try:
            return float(normalized)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _hotel_notes(item: dict) -> str:
        parts: list[str] = []
        if item.get("cityName"):
            parts.append(f"城市：{item['cityName']}")
        if item.get("star"):
            parts.append(f"档次：{item['star']}")
        if item.get("decorationTime"):
            parts.append(f"装修：{item['decorationTime']}")
        if item.get("address"):
            parts.append(f"地址：{item['address']}")
        return "；".join(parts)

    @staticmethod
    def _poi_notes(item: dict) -> str:
        parts: list[str] = []
        category = _display_poi_category(item.get("category"))
        if category:
            parts.append(f"类型：{category}")
        level = _display_poi_level(item.get("poiLevel"))
        if level:
            parts.append(f"等级：{level}")
        free_status = _display_free_status(item.get("freePoiStatus"))
        if free_status:
            parts.append(f"门票：{free_status}")
        if item.get("address"):
            parts.append(f"地址：{item['address']}")
        return "；".join(parts)

    @staticmethod
    def _keyword_hotel_notes(info: dict) -> str:
        parts: list[str] = []
        if info.get("cityName"):
            parts.append(f"城市：{info['cityName']}")
        if info.get("star"):
            parts.append(f"星级：{info['star']}")
        if info.get("scoreDesc"):
            parts.append(f"口碑：{info['scoreDesc']}")
        return "；".join(parts)

    @staticmethod
    def _transport_label(outbound: dict, inbound: dict) -> str:
        first = f"{outbound.get('marketingTransportName', '')}{outbound.get('marketingTransportNo', '')}".strip()
        second = f"{inbound.get('marketingTransportName', '')}{inbound.get('marketingTransportNo', '')}".strip()
        dep = str(outbound.get("depDateTime", "")).replace(":00", "")
        arr = str(inbound.get("arrDateTime", "")).replace(":00", "")
        return f"{first} 去程 / {second} 回程（{dep} - {arr}）"

    @staticmethod
    def _flight_matches(outbound: dict, inbound: dict, departure_city: str, destination: str) -> bool:
        dep_name = str(outbound.get("depCityName", "")).strip()
        arr_name = str(outbound.get("arrCityName", "")).strip()
        return_dep = str(inbound.get("depCityName", "")).strip()
        return_arr = str(inbound.get("arrCityName", "")).strip()
        dep_tokens = _city_tokens(departure_city)
        dest_tokens = _city_tokens(destination)
        return (
            _contains_any(dep_name, dep_tokens)
            and _contains_any(arr_name, dest_tokens)
            and _contains_any(return_dep, dest_tokens)
            and _contains_any(return_arr, dep_tokens)
        )

    @staticmethod
    def _build_sample_dates(start: date, end: date, trip_days: int, max_samples: int) -> list[date]:
        samples: list[date] = []
        current = start
        step = 7
        while current <= end and len(samples) < max_samples:
            if current + timedelta(days=trip_days - 1) <= end:
                samples.append(current)
            current += timedelta(days=step)
        if start not in samples and start + timedelta(days=trip_days - 1) <= end:
            samples.insert(0, start)
        deduped: list[date] = []
        seen = set()
        for item in samples:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return deduped[:max_samples]

    @staticmethod
    def _discover_command() -> str:
        if DEFAULT_FLYAI_PATH.exists():
            return str(DEFAULT_FLYAI_PATH)
        discovered = shutil.which("flyai.cmd") or shutil.which("flyai")
        return discovered or ""

    def _cache_key(self, args: list[str]) -> str:
        joined = "\u001f".join([CACHE_VERSION, self.command, *args])
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()

    def _cache_path(self, cache_key: str) -> Path:
        return self.cache_dir / f"{cache_key}.json"

    def _load_cached_payload(self, cache_key: str) -> dict | None:
        cache_path = self._cache_path(cache_key)
        if not cache_path.exists():
            return None
        try:
            record = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        created_at = float(record.get("created_at", 0) or 0)
        if not created_at or time.time() - created_at > self.cache_ttl_seconds:
            return None

        payload = record.get("payload")
        return payload if isinstance(payload, dict) else None

    def _write_cached_payload(self, cache_key: str, payload: dict) -> None:
        cache_path = self._cache_path(cache_key)
        record = {"created_at": time.time(), "payload": payload}
        try:
            cache_path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        except OSError:
            return

    def _search_single_train_leg(
        self,
        *,
        origin: str,
        destination: str,
        travel_date: str,
        hour_start: int,
        hour_end: int,
        max_options: int,
    ) -> list[dict]:
        payload = self._run_json(
            [
                "search-train",
                "--origin",
                origin,
                "--destination",
                destination,
                "--dep-date",
                travel_date,
                "--journey-type",
                "1",
                "--dep-hour-start",
                str(hour_start),
                "--dep-hour-end",
                str(hour_end),
                "--sort-type",
                "6",
            ]
        )
        items = ((payload.get("data") or {}).get("itemList") or [])
        results: list[dict] = []
        for item in items[: max_options * 2]:
            journeys = item.get("journeys") or []
            if not journeys:
                continue
            segments = journeys[0].get("segments") or []
            if not segments:
                continue
            segment = segments[0]
            train_no = f"{segment.get('marketingTransportName', '')}{segment.get('marketingTransportNo', '')}".strip()
            results.append(
                {
                    "train_no": train_no,
                    "dep_at": str(segment.get("depDateTime", "")).strip(),
                    "arr_at": str(segment.get("arrDateTime", "")).strip(),
                    "price": self._safe_float(item.get("price")),
                    "jump_url": str(item.get("jumpUrl", "")).strip(),
                }
            )
        return results


def _city_tokens(raw: str) -> list[str]:
    normalized = raw.strip().lower()
    mapping = {
        "上海": ["上海", "shanghai", "sha"],
        "shanghai": ["上海", "shanghai", "sha"],
        "sha": ["上海", "shanghai", "sha"],
        "大阪": ["大阪", "osaka", "osa"],
        "osaka": ["大阪", "osaka", "osa"],
        "osa": ["大阪", "osaka", "osa"],
        "南京": ["南京", "nanjing", "nkg"],
        "nanjing": ["南京", "nanjing", "nkg"],
        "nkg": ["南京", "nanjing", "nkg"],
        "北京": ["北京", "beijing", "pek", "pkx"],
        "beijing": ["北京", "beijing", "pek", "pkx"],
        "东京": ["东京", "tokyo", "tyo", "hnd", "nrt"],
        "tokyo": ["东京", "tokyo", "tyo", "hnd", "nrt"],
        "tyo": ["东京", "tokyo", "tyo", "hnd", "nrt"],
        "长沙": ["长沙", "changsha", "csx"],
        "changsha": ["长沙", "changsha", "csx"],
        "杭州": ["杭州", "hangzhou", "hgh"],
        "hangzhou": ["杭州", "hangzhou", "hgh"],
        "苏州": ["苏州", "suzhou"],
        "suzhou": ["苏州", "suzhou"],
        "成都": ["成都", "chengdu", "ctu", "tfu"],
        "chengdu": ["成都", "chengdu", "ctu", "tfu"],
        "重庆": ["重庆", "chongqing", "ckg"],
        "chongqing": ["重庆", "chongqing", "ckg"],
        "西安": ["西安", "xian", "xi'an", "xiy"],
        "xian": ["西安", "xian", "xi'an", "xiy"],
    }
    return mapping.get(normalized, [raw])


def _contains_any(value: str, candidates: list[str]) -> bool:
    lower = value.lower()
    return any(token.lower() in lower for token in candidates if token)


def _hotel_keyword_score(name: str, keyword: str) -> int:
    score = 0
    lower_name = name.lower()
    lower_keyword = keyword.lower()
    if lower_keyword and lower_keyword in lower_name:
        score += 10
    for token in keyword.split():
        token = token.strip().lower()
        if token and token in lower_name:
            score += 3
    return score


def _hotel_quality_score(item: dict) -> int:
    haystack = " ".join(
        [
            str(item.get("name", "")).strip(),
            str(item.get("address", "")).strip(),
            str(item.get("interestsPoi", "")).strip(),
            str(item.get("star", "")).strip(),
        ]
    ).lower()
    score = 0
    positive = ("酒店", "宾馆", "hotel", "全季", "亚朵", "欢朋", "汉庭", "如家", "锦江", "希尔顿", "智选", "假日")
    negative = ("青年旅社", "青旅", "旅社", "民宿", "客栈", "公寓", "电竞", "太空舱", "hostel", "guesthouse")
    if any(token in haystack for token in positive):
        score += 35
    if any(token in haystack for token in negative):
        score -= 120
    if "经济型" in haystack:
        score -= 15
    price = FlyAISearchAdapter._safe_float(item.get("price"))
    if 0 < price < 80:
        score -= 25
    elif 80 <= price < 150:
        score -= 10
    elif 150 <= price <= 1200:
        score += 8
    return score


def _looks_like_hotel_product(info: dict) -> bool:
    haystack = " ".join(
        [
            str(info.get("title", "")).strip(),
            str(info.get("subTitle", "")).strip(),
            str(info.get("areaName", "")).strip(),
        ]
    ).lower()
    bad_tokens = ("电话卡", "签证", "门票", "一日游", "接送", "演出", "套餐", "包车", "租车", "流量卡")
    if any(token in haystack for token in bad_tokens):
        return False
    hotel_tokens = ("酒店", "宾馆", "hotel", "全季", "亚朵", "欢朋", "汉庭", "如家", "锦江", "希尔顿")
    return any(token in haystack for token in hotel_tokens)


def _token_match_score(value: str, tokens: list[str]) -> int:
    lower = value.lower()
    score = 0
    for token in tokens:
        token = str(token or "").strip().lower()
        if token and token in lower:
            score += 10
    return score


def _hotel_destination_score(item: dict, dest_tokens: list[str]) -> int:
    haystack = " ".join(
        [
            str(item.get("name", "")).strip(),
            str(item.get("address", "")).strip(),
            str(item.get("interestsPoi", "")).strip(),
            str(item.get("cityName", "")).strip(),
        ]
    )
    return _token_match_score(haystack, dest_tokens)


def _keyword_hotel_destination_score(info: dict, dest_tokens: list[str]) -> int:
    haystack = " ".join(
        [
            str(info.get("title", "")).strip(),
            str(info.get("subTitle", "")).strip(),
            str(info.get("areaName", "")).strip(),
            str(info.get("cityName", "")).strip(),
        ]
    )
    return _token_match_score(haystack, dest_tokens)


def _poi_destination_score(item: dict, dest_tokens: list[str]) -> int:
    haystack = " ".join(
        [
            str(item.get("name", "")).strip(),
            str(item.get("address", "")).strip(),
            str(item.get("cityName", "")).strip(),
            str(item.get("districtName", "")).strip(),
        ]
    )
    return _token_match_score(haystack, dest_tokens)


def _extract_destination_hint(query: str) -> str:
    text = str(query or "").strip()
    if "酒店" in text:
        return text.split("酒店", 1)[0].split()[-1]
    if "宾馆" in text:
        return text.split("宾馆", 1)[0].split()[-1]
    parts = text.split()
    return parts[0] if parts else ""
