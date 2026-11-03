# 加密永續合約當沖交易幫手

自用工具：**回測 → 驗證 → 即時訊號 → 部位計算**，一條龍。只產生下單計畫，**不會自動下單**（安全第一）。

## 安裝（只需 Python 3.11+）
```bash
pip install pandas numpy
cd trading_assistant
```

## 五個指令
| 指令 | 用途 |
|---|---|
| `python main.py backtest` | 回測 config 裡所有幣種，產出 `reports/*.html` 報告 |
| `python main.py optimize --symbol BTCUSDT` | 參數敏感度：看是「高原」還是「尖刺」 |
| `python main.py wf --symbol BTCUSDT` | Walk-forward 樣本外驗證（實盤前必做） |
| `python main.py scan` / `scan --loop` | 掃描最新 K 線訊號，給出進場／停損／停利／數量 |
| `python main.py size 65000 64500` | 部位計算機：輸入進場、停損 → 數量、保證金、強平價 |

- 加 `--demo` 用模擬資料離線試跑；`--days 180` 改回測天數；`--refresh` 重抓資料。
- 資料來自 Binance 永續公開 API（免金鑰），快取在 `data/`。

## 整體邏輯
```
資料(K線+資金費率) → 指標(ATR/ADX/VWAP/量比)
   → 市況判斷：ADX 高＝趨勢 → ORB 突破；ADX 低＝盤整 → VWAP 回歸
   → 風控：固定 % 風險反推數量、槓桿上限、強平價檢查、手續費佔比過濾、日虧損上限
   → 回測引擎(保守撮合) → 績效報告 / Walk-forward → 實盤掃描
```

### 策略
- **ORB 時段開盤突破**：UTC 0/7/13 點（亞／歐／美盤）開盤後 30 分鐘形成區間，之後 2 小時內「帶量 + ADX≥20」收盤突破才進；停損 1.5×ATR，停利 2R。
- **VWAP 回歸**：ADX<20 盤整時，價格偏離 VWAP 超過 2×ATR 後出現反轉 K 收回才進，目標回到 VWAP；空間不足 1.2R 不做。
- 兩策略同根訊號衝突（一多一空）→ 都不做。

### 回測的保守假設（避免回測好看、實盤爆掉）
- 收盤確認訊號，**下一根開盤**成交，含滑價與 taker 費
- 同根同時碰停損與停利 → **算停損**；跳空穿停損 → 用更差的開盤價
- 計入每 8 小時資金費率；跨日切點強平；超時出場
- 有測試確認：不偷看未來資料、帳務對得上（`python -m unittest discover -s tests`）

## 實盤前檢查清單
1. 至少 180 天資料回測，期望值 > 0.1R、獲利因子 > 1.3
2. Walk-forward 樣本外獲利段數過半
3. 參數微調後績效不崩（optimize 結果前幾名參數相近）
4. 多幣種都不差，而不是只有一個幣好看
5. 先用最小部位實盤 2～4 週，對照回測差異

## 推播（選用）
`config.toml` 設 `telegram = true`，並設定環境變數 `TG_TOKEN`、`TG_CHAT_ID`。

## 擴充新策略
在 `ta/strategies.py` 寫一個回傳 `signal/stop/target/strategy` 的函式，加進 `REGISTRY`，並在 `config.toml` 加 `[strategy.名稱]` 區塊即可。

> ⚠️ 本工具僅供研究與個人紀律輔助，不構成投資建議。高槓桿合約可能損失全部保證金。
