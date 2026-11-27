# 三時期 CCD（plant-isce3）

[PLAnT-ISCE3](../src/plant-isce3/) を使って、3 時期の SAR SLC からコヒーレンス変化検出（CCD: Coherence Change Detection）を行う **ブラウザ GUI 付き** ツールです。

- 入力: **Sentinel-1 IW SLC（zip）** または **NISAR RSLC（HDF5）** を 3 シーン
- 出力: γpre = γ(t1, t2)、γco = γ(t2, t3)、Δγ、比、RGB 合成（GeoTIFF + 地図プレビュー）
- プロジェクト単位で `projects/<名前>/` に設定・中間ファイル・成果物を分けて保存

## 必要環境

conda（miniforge）で環境 `plant-isce3` を作ります（conda / mamba 以外は使いません）。

```bash
cd plant-isce3/ccd3
mamba env create -f environment.yml      # または conda env create -f environment.yml
conda activate plant-isce3
```

主な依存: plant-isce3 / isce3 / plant（conda-forge）、s1reader、sentineleof、GDAL、PyYAML、Pydantic 2。
GUI はサーバー側が Python 標準ライブラリの `http.server`、ブラウザ側が Vanilla JS と同梱の Leaflet 1.9.4（`static/vendor/leaflet/`）です。

## 起動

```bash
conda activate plant-isce3
python run_ccd3_gui.py                      # http://127.0.0.1:8790 をブラウザで開く
python run_ccd3_gui.py --port 9000 --no-browser
```

既定は 127.0.0.1 のみで待ち受けます（ファイルブラウザでこのマシンのファイル一覧が見えるため）。ポートが使用中なら次の番号を順に試します。

## 使い方（GUI）

1. 上部の「＋ 新規」でプロジェクトを作成（`projects/<名前>/` が作られます）
2. **入力シーン**: センサーを選び、3 シーンのパスを指定（順不同。観測日時で t1 < t2 < t3 に並べ替えます）
   → 「保存してシーン情報を読む」でフットプリントとバーストを地図に表示
3. **ROI**: 次のいずれかで指定（未指定なら 3 シーンの共通範囲全体）
   - 「□ 矩形を描画」→ 地図上でドラッグ
   - 「◎ 地図で中心をクリック」→ 東西・南北の大きさ [km]（「正方形」で連動）の矩形を作成。中心の緯度経度を数値で入れて「この中心と大きさで設定」も可
   - 四隅の経緯度を直接入力 / 「共通範囲にする」
   - Sentinel-1 では地図上のバーストをクリックすると、処理対象のバーストを明示的に選べます（空なら ROI にかかる共通バーストを自動選択）
4. **出力グリッド**: 既定は ROI 中心の UTM、10 m
5. **DEM**: Copernicus GLO-30 の自動取得、またはファイル指定（ジオイド高なら楕円体高に自動変換）
6. **軌道（Sentinel-1）**: 自動取得（POEORB → RESORB）/ ファイル指定 / SAFE 内の軌道
7. 「▶ 実行（続きから）」。進捗は下部のステップ一覧とログに表示されます。完了すると右上のパネルで成果物を地図に重ねて表示できます

設定を変えて再実行すると、影響を受けるステップ以降だけが再計算されます（例: 窓サイズの変更ならコヒーレンス計算から）。「このステップから再実行」で強制的にやり直すこともできます。

### 軌道の自動取得に必要な認証

sentineleof を使います。次のどちらかを用意してください。

- `~/.netrc` に Copernicus Data Space（`machine dataspace.copernicus.eu`）または NASA Earthdata（`machine urs.earthdata.nasa.gov`）を登録
- GUI の「認証情報を入力する」に入力（その実行のときだけ処理プロセスへ環境変数で渡し、ファイルには保存しません）

## コマンドライン

```bash
python run_ccd3.py create myproj            # プロジェクト作成 → projects/myproj/project.yaml を編集
python run_ccd3.py check myproj             # 実行前チェック
python run_ccd3.py run myproj               # 続きから実行（--from-step STEP / --only STEP）
python run_ccd3.py status myproj
```

## 処理の流れ

| ステップ | 内容 |
|---------|------|
| inspect | シーンの観測日時・軌道・フットプリント（S1 はバースト）を読み、日時順に並べる |
| orbit | S1 の軌道ファイルを用意（NISAR はプロダクト内の軌道） |
| dem | ROI + 余白の DEM を作成し、WGS84 楕円体高（EPSG:4326）に変換 |
| grid | 出力グリッドを決定。S1 はバーストごとに ROI グリッドの画素境界に揃えた部分グリッドを作る |
| geocode | 各時期を同一グリッドに複素ジオコード（距離位相の平坦化込み）。S1 は `plant_isce3_geocode --gslc --interp --terrain none` に、TOPS 方位キャリアで deramp したバースト SLC を渡す。NISAR は `isce3.geocode.geocode_slc`（NISAR GSLC ワークフローと同じ方式）で、ROI に必要な範囲だけを HDF5 から読む |
| coherence | 矩形窓でコヒーレンスを推定し、バーストをモザイク（重なりは平均、端は削る） |
| products | Δγ・比・RGB と、Web メルカトルのプレビュー PNG を作成 |

Sentinel-1 は幾何（精密軌道 + DEM）だけで同じ地図グリッドに載せます。

NISAR は幾何だけだと日付間に分解能程度（数 m）の位置ずれが残ることがあるため、ROI 中心で t2 を基準に t1・t3 の残差オフセットを振幅相関で推定し、ジオコード時に補正します（`work/coreg.json`）。相関が 0.15 未満のときは補正せず、ログに注意を出します。この注意が出た場合、どちらかのシーンのフォーカス不良などでデータ自体が比較できない可能性が高く、CCD 結果は信頼できません。

NISAR の RSLC はゼロドップラー幾何で処理されていますが、方位スペクトルの中心はネイティブドップラー（数百〜1000 Hz 程度）にあります。plant-isce3 の geocode はこれを考慮しないため、NISAR では isce3 の `geocode_slc` を使っています。

## プロジェクトのディレクトリ構成

```
projects/<名前>/
├── project.yaml          設定（GUI から保存。手で編集しても可）
├── state.json            各ステップの状態
├── logs/run_*.log        実行ログ
├── work/                 中間生成物
│   ├── scenes.json  grid.json  orbits.json  dem.json  coreg.json
│   ├── orbits/           自動取得した EOF
│   ├── dem/              dem_wgs84_ellipsoid.tif
│   ├── grid/             plant-isce3 に渡すグリッド定義（VRT）
│   ├── slc/<t>/          S1 の deramp 済み SLC（「中間ファイルを残す」のとき）
│   ├── gslc/<t>/         ジオコード済み複素画像（バースト／フレームごと）
│   └── coh/<pre|co>/     部分グリッドのコヒーレンス
└── output/               最終成果物
    ├── gamma_pre.tif     γ(t1, t2)
    ├── gamma_co.tif      γ(t2, t3)
    ├── dgamma.tif        γpre − γco（大きいほどコヒーレンス低下）
    ├── ratio.tif         γco / γpre
    ├── ccd_rgb.tif       R=γpre, G=B=γco（変化域が赤）
    ├── summary.json
    └── preview/          地図表示用 PNG
```

## 注意

- Sentinel-1 は **zip** で指定してください（plant-isce3 が SAFE ディレクトリに対応していないため）。
- 国土地理院 DEM（標高）は EGM2008 で近似して楕円体高に変換します（差は日本付近で 1 m 程度）。
- ジオイド補正には PROJ のジオイドグリッドをネットワーク取得します（`PROJ_NETWORK=ON` を自動設定）。オフラインで使う場合は事前に `projsync` で取得してください。
- 大きな ROI では、モザイク時に ROI 全体の配列をメモリに載せます（例: 100 km 四方・10 m で数 GB）。
- GUI サーバーを終了しても、実行中の処理はバックグラウンドで続きます。再起動後に状態が反映されます。

## テスト

```bash
conda activate plant-isce3
python -m pytest -q tests
```

`tests/test_pipeline_nisar.py` は plant-isce3 同梱の `envisat.h5`（NISAR RSLC 形式）で、DEM → グリッド → ジオコード → コヒーレンス → 成果物を通しで確認します。
