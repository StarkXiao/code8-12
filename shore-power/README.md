# 港口岸电使用核算系统

记录船舶的**接电时长**与**用电量**，按**航次**出具对账明细，供**船方与港方核对确认**。

纯 Python 3 标准库实现（SQLite 存储），无任何第三方依赖。

## 功能概览

- **接电记录**：接电/断电时间自动计算接电时长；电表起码/止码自动计算用电量（kWh）
- **电价方案**：电费单价 + 服务费单价，按生效日期自动取值，调价不影响历史账单
- **航次对账**：按航次汇总生成对账单，明细为**快照**，出具后不随后续数据修改而变化
- **双方确认**：状态机驱动的核对流程，支持船方异议退回、港方修订后重新出具（版本递增）
- **多种接口**：命令行（CLI）、HTTP JSON API、可打印的 HTML 对账单

## 对账流程

```
草稿 ──出具──▶ 已出具 ──船方确认──▶ 船方已确认 ──港方确认──▶ 办结
 ▲              │                                           
 │              └──船方异议──▶ 异议(港方修订,版本+1重新出具)  
 └──作废◀──(仅草稿/异议可作废)                               
```

规则要点：

- 每个航次同一时间只允许一个进行中的对账单
- 已出具/已确认/已办结的对账单会**锁定**其引用的接电记录，禁止修改删除
- 船方异议后接电记录解锁，修正数据时对账单自动重算，重新出具版本号 +1
- 已办结后如补录接电记录，可就该航次再出**补充对账单**（只含未入账明细）

## 快速开始

```bash
cd shore-power
python3 demo.py          # 端到端演示：登记→接电→出账→双方确认→导出HTML
python3 -m unittest discover -s tests   # 运行测试（17 个用例）
```

## CLI 用法

```bash
# 基础资料
python3 -m shorepower config-port --name "滨海港国际集装箱码头"
python3 -m shorepower tariff-add --name 岸电标准电价 --energy-price 0.85 \
    --service-price 0.20 --effective-from 2026-01-01
python3 -m shorepower vessel-add --name 远洋之星 --imo 9876543 --company 蓝海航运
python3 -m shorepower voyage-add --vessel 远洋之星 --voyage-no VY2026-0918 \
    --berth 3号泊位 --arrived-at "2026-09-18 14:00"

# 接电 / 断电（自动算时长与用电量）
python3 -m shorepower connect --voyage-id 1 --meter-no M-3A-01 \
    --at "2026-09-18 15:10" --meter-start 10234.50
python3 -m shorepower disconnect --session-id 1 \
    --at "2026-09-18 23:40" --meter-end 10512.80

# 对账
python3 -m shorepower statement-create --voyage-id 1      # 生成草稿
python3 -m shorepower statement-show --id 1 --html out.html
python3 -m shorepower statement-issue --id 1              # 出具，待船方确认
python3 -m shorepower statement-ship-confirm --id 1 --by 王船长
python3 -m shorepower statement-port-confirm --id 1 --by 李调度   # 办结

# 船方有异议时
python3 -m shorepower statement-ship-dispute --id 1 --reason "止码抄录有误"
python3 -m shorepower session-edit --id 1 --meter-end 10520.00    # 港方修正
python3 -m shorepower statement-issue --id 1              # 重新出具（V2）
```

全局参数 `--db 路径` 或环境变量 `SHORE_POWER_DB` 可指定数据库文件（默认 `./shore_power.db`）。

## HTTP API

```bash
python3 -m shorepower serve --host 0.0.0.0 --port 8000
```

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST | `/api/vessels` `/api/voyages` `/api/tariffs` | 基础资料 |
| POST | `/api/sessions/start` `/api/sessions/{id}/stop` | 接电 / 断电 |
| POST | `/api/statements` | 按航次生成对账单 `{voyage_id}` |
| GET | `/api/statements/{id}` | 对账明细（JSON） |
| GET | `/api/statements/{id}/html` | 对账单（可打印页面） |
| POST | `/api/statements/{id}/issue` | 出具 |
| POST | `/api/statements/{id}/ship-confirm` | 船方确认 `{confirmer}` |
| POST | `/api/statements/{id}/ship-dispute` | 船方异议 `{reason}` |
| POST | `/api/statements/{id}/port-confirm` | 港方确认办结 `{confirmer}` |
| POST | `/api/statements/{id}/refresh` `/cancel` | 重新汇总 / 作废 |

## 核算规则

- **接电时长** = 断电时间 − 接电时间（分钟取整）
- **用电量** = 电表止码 − 起码（保留 0.01 kWh）
- **电费** = 用电量 × 电费单价；**服务费** = 用电量 × 服务费单价
- 金额按明细逐条计算、四舍五入到**分**，合计为明细之和（避免合计误差）
- 电价按**接电时间**之前最近生效的方案取值

## 目录结构

```
shore-power/
├── shorepower/
│   ├── db.py         # SQLite 表结构与连接
│   ├── services.py   # 业务逻辑：会话/对账/确认状态机
│   ├── report.py     # 文本 + HTML 对账单渲染
│   ├── cli.py        # 命令行入口
│   └── server.py     # HTTP JSON API（标准库）
├── tests/test_services.py   # 17 个单元测试
├── demo.py                  # 端到端演示
└── README.md
```
