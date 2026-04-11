# Travel Control Tower 开发说明

## 当前在做什么

这个项目先做旅行规划引擎，再做正式前端。

当前目标不是 OTA，也不是自动下单，而是把一趟旅行整理成一套可以直接执行的结果：

- 完整旅行规划
- 每天怎么走
- 每天吃什么
- 预算拆分
- 预定事项和链接
- 甘特图和导出

## 为什么先做引擎

如果先做页面，后面输入结构、预算结构、行程结构一改，前端和导出都会返工。

所以当前顺序固定为：

1. 定输入结构
2. 定输出结构
3. 做规划引擎
4. 接地图和搜索适配器
5. 做本地预览
6. 再做正式 Web 页面

## 当前 MVP 范围

第一阶段只做一条闭环：

- 用户填写基本出行信息
- 生成一套主方案
- 展示每日行程、预算、预定事项
- 输出本地 HTML 预览

第一阶段明确不做：

- 自动下单
- 支付
- OCR 导入
- 多人协作
- 一次生成很多平行方案

## 当前目录

```text
travel_control_tower/
  contracts/            输入输出 schema
  adapters/             外部数据适配器
  planner_core/         规划引擎
  preview/              本地 HTML 预览
  web/                  最小 Web 页面
  examples/             示例输入输出
  tests/                基础测试
  docs/                 开发说明、决策、待办
```

## 本地配置

配置文件位置：

`C:\Users\Admin\.codex\travel-control-tower.json`

示例见：

`travel_control_tower/docs/local_config.example.json`

当前支持：

- `google_maps_api_key`
- `amap_web_key`
- `flyai_cmd`
- `preview_port`
- `web_port`

## 当前验证方式

### 方式 1：固定示例预览

命令：

```bash
python -m travel_control_tower.preview.serve_preview
```

地址：

`http://127.0.0.1:8766/japan_osaka_weekend.preview.html`

### 方式 2：本地 Web 表单

命令：

```bash
python -m travel_control_tower.web.app
```

地址：

`http://127.0.0.1:8770/`

这个页面适合当前阶段验收，因为不需要看代码，只需要填参数、点生成、看结果。

## 每轮开发的交付要求

每轮至少要回答四件事：

1. 改了什么
2. 为什么这么改
3. 这轮重点看哪些文件
4. 下一轮准备做什么

## 当前已跑通的链路

- TripRequest 归一化
- TripPlan 结构化输出
- Osaka 周末场景模板
- 路线说明和机动缓冲拆分
- 预算拆分
- 预定清单
- 本地 HTML 预览
- 本地 Web 表单生成

## 当前还没完成的部分

- 真实地图时间还没有完全写回所有时间块
- FlyAI 搜索在 Windows 下还不够稳定
- 预算还没有完全按真实机酒价格反推
- 正式前端还没开始
