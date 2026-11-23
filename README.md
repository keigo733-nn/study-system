# 國考讀書系統：自動題庫

這個專案每 6 小時自動執行一次，做三件事：

1. 照考選部官方的「考畢試題連結網址」清單，下載試題和答案 PDF。
2. 拆成一題一題、對上答案（有更正答案就用更正版）、標出題目裡的法條。
3. 檢查沒問題的試卷才上架到題庫網站，讀書系統打開時會自動抓。

第一次要把 101 年以來約 3.8 萬份試卷全部抓完，為了不造成考選部網站負擔，每次下載間隔 1 秒，**大約需要 2～3 天**。之後只會抓新公布的試卷，幾秒鐘就跑完。

---

## 一次性設定（約 10 分鐘）

### 1. 註冊 GitHub 帳號
到 https://github.com 免費註冊。

### 2. 建立專案
登入後按右上角 **＋ → New repository**：
- Repository name：`study-system`
- 選 **Public**（公開專案才能免費使用自動排程和網站）
- 按 **Create repository**

### 3. 上傳檔案
在新專案頁面按 **uploading an existing file**，把這個資料夾裡的下列項目**整個拖進去**：
`tools`、`data`、`laws`、`docs`、`README.md`
等上傳完成後，按下方的 **Commit changes**。

### 4. 建立自動排程檔
資料夾名稱開頭有「.」的檔案，網頁上傳常常會被略過，所以這個檔案用手動建立：
1. 在專案首頁按 **Add file → Create new file**
2. 檔名欄位輸入：`.github/workflows/update-exams.yml`（打到 `/` 時會自動變成資料夾，這是正常的）
3. 用記事本打開這個資料夾裡的 `設定用/update-exams.yml`，全部複製，貼到大的輸入框
4. 按 **Commit changes**

### 5. 開啟網站（GitHub Pages）
**Settings → Pages**：
- Source 選 **Deploy from a branch**
- Branch 選 **main**，資料夾選 **/docs**，按 **Save**

幾分鐘後，網站就會出現在：`https://你的帳號.github.io/study-system/`
（這就是線上版的讀書系統，手機也能開。）

### 6. 第一次手動執行
**Actions** 分頁 → 如果出現提示，先按綠色按鈕啟用 → 左邊點 **更新考古題題庫** → 右邊 **Run workflow**。
之後每 6 小時會自動執行，不用再管它。

### 7. 讓電腦上的讀書系統連到題庫
打開你電腦上的 `國考讀書系統.html` → **設定** → **題庫網址** 填：
`https://你的帳號.github.io/study-system/exams/`
按儲存。（用線上版的話不用填，會自動連。）

---

## 選用：讓清單也自動更新
考選部的清單每次考試後會增加新的試卷。設定一次下載連結，排程就會自動拿最新清單：
1. 到政府資料開放平台的資料集頁面（https://data.gov.tw/dataset/170565），在 CSV 下載按鈕上**按右鍵 → 複製連結網址**
2. 專案的 **Settings → Secrets and variables → Actions → Variables → New repository variable**
3. Name 填 `CSV_URL`，Value 貼上剛剛的連結，儲存

沒設定的話，就沿用目前這份清單（到 115 年為止）。

---

## 看執行結果
- **Actions** 分頁：每次執行的紀錄，綠色勾勾表示成功。
- `docs/exams/report.md`：本次成功幾份、失敗幾份、失敗原因。
- 失敗的試卷（例如年代較久的掃描檔）不會上架。把 `report.md` 給 Claude 看，修好解析程式後再重跑。

## 資料來源與授權
- 試題：考選部考畢試題。依著作權法第 9 條，依法令舉行之考試試題不得為著作權之標的。
- 法規：全國法規資料庫（https://law.moj.gov.tw/），依政府資料開放授權條款-第1版利用。
- 內容僅供學習參考，以官方公布為準。

## 檔案說明
| 檔案 | 用途 |
|---|---|
| `tools/exam_pipeline.py` | 抓題主程式（可中斷續跑） |
| `tools/parse_exam.py` | 解析單份試題／答案 PDF |
| `tools/build_laws.py` | 把全國法規資料庫的 FalV.xml 轉成法規資料 |
| `tools/build_single.py` | 打包成單一 HTML 檔 |
| `data/ExamQandA_Csv.csv.gz` | 考選部官方清單（壓縮檔） |
| `laws/index.json` | 法規目錄（辨識題目中的法條用） |
| `docs/index.html` | 線上版讀書系統 |
| `docs/exams/` | 自動產生的題庫（不要手動修改） |
