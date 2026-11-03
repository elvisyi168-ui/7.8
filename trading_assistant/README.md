# 多市場當沖交易幫手（台股／美股／加密永續合約）

自用整合系統：**回測 → 驗證 → 即時訊號 → Time Machine → 部位 → 風控 → PASS/BLOCK 下單單**。
只產生下單單，**不會自動下單**；下單由你本人在券商／交易所完成。

## 安裝（Python 3.11+）
```bash
pip install pandas numpy
cd trading_assistant
python -m unittest discover -s tests      # 25 項測試，離線可跑
```

## 指令（`--market crypto / us / tw / all`，可逗號串接；加 `--demo` 用模擬資料）
| 指令 | 用途 |
|---|---|
| `python main.py plan` | **主流程**：三個市場一起掃描，每個訊號產出一張 PASS/BLOCK 下單單 → `trader/tickets.json` |
| `python main.py plan --loop` | 每根 K 收盤自動跑（只掃開盤中的市場），PASS 可推播 Telegram |
| `python main.py tm --market us --symbol TSLA` | Time Machine：最近一次訊號的誠實機率 |
| `python main.py tm --market us --symbol TSLA --entry 364.18 --stop 358.32 --target 370.04 --at 10:15` | Time Machine：手動指定一單 |
| `python main.py backtest --market all` | 回測並產出 `reports/*.html` |
| `python main.py optimize --market tw --symbol 2330.TW` | 參數敏感度 + **停損模式比較**（ATR／區間中點／區間另一端） |
| `python main.py wf --market crypto --symbol BTCUSDT` | Walk-forward 樣本外驗證 |
| `python main.py size --market tw 1000 985` | 部位計算機（含最小單位、最低手續費、稅、強平價） |
| `python main.py log --market us --symbol TSLA --pnl -46.88 --R -1` | 記錄已平倉交易（日虧損上限、每日筆數會讀它） |

## 三個市場的差異（全在 `config.toml`，改設定不用改程式）
| | 加密永續 | 美股 | 台股 |
|---|---|---|---|
| 資料 | Binance 公開 API | Yahoo（5m 約 60 天） | Yahoo（`2330.TW`、`xxxx.TWO`） |
| 時段 | 24h，UTC 0 點日切 | 09:30–16:00 紐約（自動夏令） | 09:00–13:30 台北 |
| 開盤區間 | 亞／歐／美盤 00/07/13 UTC 各一次 | 09:30 起 15 分鐘 | 09:00 起 15 分鐘 |
| 強制出場 | 日切點、最長持倉 | 15:55 | 13:20（避開收盤集合競價） |
| 最後進場 | — | 15:00 | 12:30 |
| 成本 | taker 0.05%／maker 0.02% + 資金費率 | 零佣金 + 賣出規費 | 手續費 0.1425%×折扣（最低 20 元）+ 當沖證交稅 0.15% |
| 單位 | 不取整 | 整股 | 零股（整張改 `qty_step = 1000`） |
| 放空 | 允許 | 預設關（需融券） | 預設關（需當沖資格） |
| 槓桿 | 10x（強平檢查） | 1x | 1x |

## 下單單流程（一單一流程）
```
訊號（K 收盤確認） → 出場計畫（停損／停利／出場期限）
 → Time Machine：① 用過去同時段的真實 K 線重組剩下走勢 10,000 次 ② 同策略歷史重播（含成本）
 → 部位計算 → 風控清單逐條檢查 → 全過 = PASS，任一不過 = BLOCK（附原因）
```
風控清單：交易時段、可否放空、停損方向、部位成立、單筆虧損上限、R:R 下限、今日虧損上限、今日筆數、Time Machine 不得為負期望。

## 策略
- **ORB 開盤區間突破**：區間形成後，帶量（量比 ≥1.5）+ ADX ≥20 的收盤突破；每時段每方向一次。
  停損模式 `stop_mode`：`atr`（1.5×ATR）／`or_mid`（區間中點，美股台股預設）／`or_low`（區間另一端）。
- **VWAP 回歸**：ADX <20 盤整時，偏離 VWAP 超過 2×ATR 後反轉收回才進，目標 VWAP。
- 同根一多一空 → 都不做；市場不允許放空 → 剔除空單。

## 回測的保守假設
下一根開盤成交（含滑價）、同根碰停損停利算停損、跳空用開盤價、賣出加稅、最低手續費、資金費率、強平。
測試涵蓋：不偷看未來、帳務對得上、美股收盤前平倉、時區／夏令時間、成本計算。

## 重要觀察（模擬資料已顯示）
- **台股當沖成本約 0.32% 來回**，5 分 K 窄停損幾乎都被「成本佔風險 >15%」擋下。台股要嘛用更寬的停損／更長週期，要嘛只做波動夠大的股票；也可把 `[markets.tw.account] max_fee_r` 放寬到 0.25（請先回測）。
- 加密合約手續費 + 高頻進出同樣吃利潤，回測會誠實反映。

## 實盤前檢查清單
1. 每個市場至少 60 天（美台股 Yahoo 上限）／180 天（加密）回測，期望值 > 0.1R、獲利因子 > 1.3
2. Walk-forward 樣本外獲利段數過半
3. optimize 前幾名參數相近（高原而非尖刺）；停損模式用數據選
4. 多商品都不差
5. 最小部位實盤 2～4 週，每筆用 `log` 記錄，對照回測差異

## 限制
- 未處理國定假日、台股漲跌停與升降單位、美股盤前盤後
- Yahoo 資料為延遲／非官方來源，僅供研究；強平價為近似值
- Telegram 推播：`[defaults.notify] telegram = true` + 環境變數 `TG_TOKEN`、`TG_CHAT_ID`

## 擴充
- 新策略：`ta/strategies.py` 寫函式 `(df, params, market) → signal/stop/target/strategy`，加進 `REGISTRY`，config 加區塊。
- 新市場（例如期貨）：`config.toml` 新增 `[markets.xxx]`；新資料源在 `ta/data.py` 的 `FETCHERS` 註冊。

> ⚠️ 僅供研究與個人紀律輔助，不構成投資建議。槓桿商品可能損失全部保證金。
