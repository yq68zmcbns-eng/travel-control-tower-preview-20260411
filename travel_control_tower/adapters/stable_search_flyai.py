from __future__ import annotations

import json
import subprocess

from .base import TransportCandidate
from .search_flyai import FlyAISearchAdapter


def _transport_city_name(raw: str) -> str:
    normalized = str(raw or "").strip().lower()
    mapping = {
        "上海": "Shanghai",
        "上海市": "Shanghai",
        "shanghai": "Shanghai",
        "sha": "Shanghai",
        "苏州": "Suzhou",
        "苏州市": "Suzhou",
        "suzhou": "Suzhou",
        "南京": "Nanjing",
        "南京市": "Nanjing",
        "nanjing": "Nanjing",
        "北京": "Beijing",
        "北京市": "Beijing",
        "beijing": "Beijing",
        "杭州": "Hangzhou",
        "杭州市": "Hangzhou",
        "hangzhou": "Hangzhou",
        "成都": "Chengdu",
        "成都市": "Chengdu",
        "chengdu": "Chengdu",
        "重庆": "Chongqing",
        "重庆市": "Chongqing",
        "chongqing": "Chongqing",
        "西安": "Xian",
        "西安市": "Xian",
        "xian": "Xian",
        "xi'an": "Xian",
        "长沙": "Changsha",
        "长沙市": "Changsha",
        "changsha": "Changsha",
        "东京": "Tokyo",
        "tokyo": "Tokyo",
        "大阪": "Osaka",
        "osaka": "Osaka",
        "京都": "Kyoto",
        "kyoto": "Kyoto",
    }
    return mapping.get(normalized, str(raw or "").strip())


class StableFlyAISearchAdapter(FlyAISearchAdapter):
    @staticmethod
    def _is_empty_result_payload(payload: dict) -> bool:
        status = payload.get("status")
        message = str(payload.get("message", "")).strip()
        data = payload.get("data")
        if status in (1, "1") and not data and message:
            return "结果为空" in message or "无结果" in message or "为空" in message
        return False

    @classmethod
    def _parse_payload(cls, raw: str) -> dict:
        decoder = json.JSONDecoder()
        stripped = (raw or "").strip()
        candidates: list[str] = []
        if stripped:
            candidates.append(stripped)
            try:
                extracted = cls._extract_json(stripped)
            except Exception:
                extracted = ""
            if extracted and extracted != stripped:
                candidates.append(extracted)

        last_error: Exception | None = None
        seen: set[str] = set()
        for candidate in candidates:
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            try:
                payload = json.loads(candidate)
                if isinstance(payload, dict):
                    return payload
            except Exception as exc:
                last_error = exc

            for start_index, char in enumerate(candidate):
                if char != "{":
                    continue
                try:
                    payload, _ = decoder.raw_decode(candidate[start_index:])
                except json.JSONDecodeError as exc:
                    last_error = exc
                    continue
                if isinstance(payload, dict):
                    return payload

        if last_error is not None:
            raise last_error
        raise RuntimeError("FlyAI returned no JSON payload")

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
            self.last_error = "FlyAI trial limit reached"
            raise RuntimeError(self.last_error)

        if not raw:
            self.last_error = stderr or "FlyAI returned no parseable payload"
            raise RuntimeError(self.last_error)

        try:
            payload = self._parse_payload(raw)
        except Exception as exc:
            self.last_error = f"FlyAI output parse failed: {exc}"
            raise RuntimeError(self.last_error) from exc

        error = payload.get("error")
        if isinstance(error, dict):
            message = str(error.get("message", "")).strip()
            if message:
                self.last_error = message
                raise RuntimeError(message)

        status = payload.get("status")
        message = str(payload.get("message", "")).strip().lower()
        if self._is_empty_result_payload(payload):
            self.last_error = ""
            self.last_warning = str(payload.get("message", "")).strip()
            self.last_source = "live"
            self._write_cached_payload(cache_key, payload)
            return payload
        if completed.returncode != 0 and status not in (0, "0") and message != "success":
            self.last_error = stderr or "FlyAI invocation failed"
            raise RuntimeError(self.last_error)

        self.last_error = ""
        self.last_warning = ""
        self.last_source = "live"
        self._write_cached_payload(cache_key, payload)
        return payload

    def _flight_matches_any(
        self,
        outbound: dict,
        inbound: dict,
        departure_city: str,
        destination: str,
        departure_query: str,
        destination_query: str,
    ) -> bool:
        return self._flight_matches(outbound, inbound, departure_city, destination) or self._flight_matches(
            outbound,
            inbound,
            departure_query,
            destination_query,
        )

    def search_transport(
        self,
        departure_city: str,
        destination: str,
        start_date: str,
        end_date: str,
    ):
        if not self.command:
            return []

        departure_query = _transport_city_name(departure_city)
        destination_query = _transport_city_name(destination)
        payload = self._run_json(
            [
                "search-flight",
                "--origin",
                departure_query,
                "--destination",
                destination_query,
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
            if not self._flight_matches_any(
                outbound,
                inbound,
                departure_city,
                destination,
                departure_query,
                destination_query,
            ):
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
    ):
        if not self.command:
            return []

        outbound = self._search_single_train_leg(
            origin=_transport_city_name(departure_city),
            destination=_transport_city_name(destination),
            travel_date=start_date,
            hour_start=outbound_hour_start,
            hour_end=outbound_hour_end,
            max_options=max_options,
        )
        inbound = self._search_single_train_leg(
            origin=_transport_city_name(destination),
            destination=_transport_city_name(departure_city),
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
                label = f"{out_item['train_no']} 去程 / {back_item['train_no']} 回程 ({out_item['dep_at'][11:16]} - {back_item['arr_at'][11:16]})"
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
