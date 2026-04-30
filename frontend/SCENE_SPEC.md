# Scene UI 仕様（固定版）

このドキュメントは `frontend/scene.html` に実装されている **Scene** の挙動を定義する。改修時は本仕様との差分を PR で明示し、逸脱はレビューで差し戻すこと。

---

## 1. 概要

- Scene は **1タスク単位の状態可視化 UI** である。
- **Intake → Routing → Agent → Runtime → Result** の 5 段階パイプラインで、タスクの流れを示す。
- **Current Task（タスクパネル）** を中心に、**Pipeline** と **Activity** を **同一フォーカスタスク** に同期して描画する。
- データは **`GET /api/internal/state`**（同一オリジン）をポーリングして取得する。実装は `scene.html` 内の単一 `<script>` に集約されている。

---

## 2. Task の定義

### 2.1 代表 ID（`taskKey`）

表示・フッター・チップ等の **主表示用 ID** は次の優先順で 1 つに決める。

1. `intake.runtime_task_id`
2. `intake.id`
3. `intake.task_id`

### 2.2 同一タスク判定（`intakeTaskIds`）

API の `task_id` と intake 行を突き合わせるときは **複数キー**を使う。

- `intakeTaskIds(item)` は上記 3 フィールドのうち **空でない値すべて**を `Set` として返す。
- `intakeMatchesEventTask(intakeItem, eventTaskId)` は `eventTaskId` がその Set に含まれるかで判定する。
- 手動選択 `selectedTaskId` も、この Set のいずれかと一致すればよい。

### 2.3 イベント側の `task_id`（`eventTaskId`）

イベントオブジェクトからは概ね次の順で ID を取る（実装参照）。

- `e.task_id`
- `e.raw_payload.raw_item.runtime_task_id`
- `e.raw_payload.task_id`

---

## 3. Task 選択ロジック

### 3.1 関数の役割

| 関数 | 役割 |
|------|------|
| `pickDisplayTask(intake, events)` | **画面のフォーカス**（Pipeline / Current Task / Activity ハイライトの基準）。戻り値 `{ task, isActive }`。 |
| `pickCurrentTask(intake, events)` | `pickDisplayTask(...).task` のエイリアス。 |
| `pickAutoOpenTask(intake, events)` | **未完了のみ**から先頭を選ぶ。Task Switcher の「◀ 現在」表示用。 |

`renderAll` では **可視 intake**（後述の除外適用後）だけを `data.intake` として各関数に渡す。

### 3.2 自動選択（`pickDisplayTask` 内）

1. `selectedTaskId` が無い、または intake に一致する行が無い場合。
2. **未完了**タスクを `updated_at` の新しい順で並べ、先頭を選ぶ（`pickAutoOpenTask` と同基準）。
3. 未完了が無ければ、**完了**タスクのうち `updated_at` が最新の 1 件をフォールバック表示し、`isActive: false` とする。

### 3.3 手動選択（最優先）

- `app.selectedTaskId` が設定されていれば、**可視 intake** の中から `taskMatchesSelection`（＝`intakeTaskIds` による一致）で行を探す。
- 見つかった行は **完了済みでも** そのままフォーカスする（`isActive` は完了判定で決める）。
- **一覧に存在しない ID** のみ `selectedTaskId` を `null` に戻す。

### 3.4 Task Switcher 操作

- ☰ **件数チップ**（`#task-count-chip`）でドロップダウンを開閉する。
- 行クリックで `selectTask(taskId)`：`data-task-id` は **`taskKey(item)`**。
- **同じ行を再クリック**すると `selectedTaskId` をトグル解除し、**自動選択に戻る**。
- 手動選択中はタスクパネルに **「手動選択」＋短縮 task_id** を表示する。Switcher 側は選択行に `ts-selected` を付与する。

---

## 4. 完了判定

以下の **いずれか**を満たせば intake 行は完了扱い（未完了リストから外れる）とする。

- `reply_draft_status === "completed"`
- `runtime_status === "completed"`（intake フィールド）
- イベント集合に、当該タスク ID について次のいずれかが含まれる  
  - `task.completed`  
  - `runtime.completed`  
  - `runtime.retry_completed`

完了 ID の集合は `buildCompletedTaskIds(events)` で構築し、**`app.taskStateMap`**（タスク単位イベントキャッシュの最新 1 件）からも完了イベント型をマージして補強する（`enrichCompletedTaskIds`）。

---

## 5. 時間表示ルール

### 5.1 共通：`computeProcessingDurationMs(taskEvents)`

**完了タスクの「所要」**および Pipeline の **実処理時間**は、**受付〜完了の壁時計**ではなく、次の **ペアの時間差**のみを使う（取れなければ `null` → 所要行は出さない）。

優先順（**後方から**最後の end イベントを探し、その前の対応 start を探す方式）:

1. `runtime.completed` と、その直前の `runtime.started`（end より前で最後のもの）
2. 上が取れなければ `runtime.retry_completed` と `runtime.retry_started`
3. 上が取れなければ `task.completed` と `task.started`

### 5.2 未完了（Current Task Panel）

- **経過**＝`now - intake.updated_at`（ミリ秒 → `fmtDur`）。
- **所要**はパネルでは使わない（Pipeline 側の進行中表示は別）。

### 5.3 完了（Current Task Panel）

- **所要**＝`computeProcessingDurationMs` の結果のみ（あれば表示）。
- **受信**＝`updated_at` を時刻表示。
- **経過は表示しない**。

---

## 6. Pipeline 仕様

フォーカスタスクについて `activeTaskMode === picked.isActive` で分岐する。

### 6.1 未完了（`activeTaskMode === true`）

| 段 | 主な行 |
|----|--------|
| Intake | 件数・カテゴリ・サービス・**受信**・**経過**（受付からの経過。完了前はカウントアップ） |
| Routing | 担当（`displayRole`）・理由・Gateway 状態（あれば）・**所要**（受信→`task.started` 近似） |
| Agent | 担当・状態・サービス・**経過**（`task.started` からの経過） |
| Runtime | サブ状態・返信状態・コネクタ・**所要**（上記 agent 経過と同系の表示） |
| Result | 返信・担当・受信・**所要**（受付起点の合計に近い値。進行中の総量表示用） |

※ 長時間はチップ色で警告寄りのスタイルになる場合がある。

### 6.2 完了（`activeTaskMode === false`）

全ステージの **state は completed** に揃え、矛盾した「進行中」表示を出さない。

| 段 | 主な行 |
|----|--------|
| Intake | 件数・カテゴリ・サービス・**受信のみ**（**経過なし**） |
| Routing | 担当・理由・状態「完了」・**経路＝即時**（`k.path` / `k.instant`）。**振り分け所要は出さない** |
| Agent | 担当・状態・サービス・**所要**＝実処理 ms（あれば） |
| Runtime | サブ状態・返信・コネクタ・**所要**＝実処理 ms（あれば） |
| Result | 返信・担当（`displayRole`）・受信・**所要**＝実処理 ms（あれば） |

**「所要 540m」のような受付起点の誤解を招く表示は、完了モードでは出さない**（実装の意図）。

---

## 7. Current Task Panel

### 7.1 未完了

- ラベル：**現在処理中**
- キッカー：`NOW`（英語モードでは `story.now` の文言）
- フィールド：**カテゴリ / サービス / 担当（生の role キー表記） / 経過**

### 7.2 完了（直近完了フォールバックまたは手動で完了行を選択）

- ラベル：**現在処理中のタスクはありません**（フォーカスは完了行）
- キッカー：`LAST DONE`
- ストーリー：**返信案が完成** 系の文言
- フィールド：**所要（実処理）＋受信**のみ。**経過は出さない**。

### 7.3 手動選択

- `selectedTaskId` がある間、バッジ横に **手動選択 · 短縮 ID** を表示する。

---

## 8. Activity 仕様

### 8.1 表示対象イベント

`FEED_TYPE` に定義された `event_type` のみ（受付・開始・完了・失敗・保留・コネクタ・runtime アラート・stuck_warning・agent 状態など）。それ以外はリストに出さない。

### 8.2 行頭の案件識別

`buildFeedRow` により、本文は必ず次のいずれかの形式で始める。

- `task_id` が取れる場合：`[⊢ <shortId(task_id)>] <main>`
- 取れない場合で category/service が取れる場合：`[<category> / <service>] <main>`

### 8.3 role ラベル

- **Activity 本文・sub から、日本語の抽象ロール名（経営企画など）に相当する装飾は出さない**方針。`describeFeedEvent` では `task.started` / `task.completed` 等で sub を空にしている。
- パイプライン・パネル・カードの担当表示は **`displayRole`** で **API の生キー**（`research` / `dev` / `ops`）をそのまま出す（i18n の役職名に依存しない）。

### 8.4 フィルタ

- intake に紐づかない `task_id` のグループは、**可視 intake の `intakeTaskIds` のいずれにも一致しない場合**は Activity に出さない。

---

## 9. 除外タスク（Ignorable）

次を満たす intake 行は **通常 UI の intake から除外**する（`visibleIntakeItems`）。ヘッダの内訳などでは「対象外」として数える。

- **`runtime_task_id` が空**かつ **runtime 系イベントが無く**、かつ **最終更新から 3 分以上**経過している、または
- イベントに **skip / no_action** 系、`task.skipped` 等の明示的スキップシグナルがある

**`?debug=1`** のときは `DEBUG_MODE` が真となり、**除外を行わない**（検証用）。

---

## 10. その他の安定化挙動（参照）

- **タスク単位イベントキャッシュ**（`taskEventMap` / `taskStateMap`）：ポーリングで取りこぼしやすい履歴をマージし、stuck / 完了判定を安定させる。
- **stuck 表示**：`runtime.stuck_warning` と完了系イベントの時刻比較、および開始から 5 分超のヒューリスティック（実装参照）。
- **fetch**：`AbortController` によるタイムアウト、失敗時は `lastData` を保持して UI を空白にしない。本仕様書ではネットワーク詳細は固定しない。

---

## 11. 禁止事項・非目標

- **PII を表示しない**（メール・電話・本文など）。`safeDetail` 等で要約・メタのみに寄せる。
- **backend の schema 変更は Scene 改修の範囲に含めない**。
- **queue 形式・`result.json` schema の変更は行わない**（本 UI は受け取った payload の表示に留める）。

---

## 12. レビュー用チェックリスト（PR で確認）

- [ ] `taskKey` / `intakeTaskIds` / `eventTaskId` の整合が崩れていないか  
- [ ] 手動選択が **完了行でも** 有効か。再クリックで解除されるか  
- [ ] 完了タスクで **受付起点の長い「所要」**が復活していないか  
- [ ] Activity 行頭の **`[⊢ …]` / `[category / service]`** が欠けていないか  
- [ ] Activity に **i18n 日本語ロール名**が紛れ込んでいないか  
- [ ] 除外ルールと `?debug=1` の挙動が意図どおりか  
- [ ] PII が新経路から漏れていないか  

---

## 変更履歴

| 日付 | 内容 |
|------|------|
| 2026-04-30 | 初版：時間表示・Task Switcher・Activity 識別子の仕様を文書化 |
