# Plant-ISCE3 エクスプローラ

plant-isce3 のコマンドをブラウザ GUI から試すための **別アプリ** です（三時期 CCD の [`../ccd3`](../ccd3) とは独立、別ポート）。

## 合意した仕様（第1版 MVP）

| 項目 | 内容 |
|------|------|
| 目的 | **単発で** plant-isce3 を試す（geocode / rtc） |
| 入力 | **NISAR RSLC（HDF5）** 中心 |
| 画面 | argparse **自動展開**フォーム + **Leaflet** で ROI |
| DEM | **国土地理院タイル自動取得**（別 env `dem_downloader` を subprocess）／Copernicus GLO-30／任意ファイル。出力は **WGS84 楕円体高** |
| プロジェクト | `projects/<名前>/` に設定・ログ・出力を保存 |
| 構成 | 1 プロジェクトで入力・ROI・DEM を共有し、**geocode / rtc をタブで別実行** |
| 起動 | `python run_workbench_gui.py`（既定ポート **8785**） |

## 使い方

```bash
# 国土地理院 DEM 用（plant-isce3 とは別環境・rasterio はこちらだけ）
cd dem_downloader && mamba env create -f environment.yml   # 初回のみ

conda activate plant-isce3
cd plant-isce3/workbench
python run_workbench_gui.py
```

国土地理院 DEM は **`dem_downloader` 環境の Python を subprocess で呼び出します**（`plant-isce3` に rasterio を入れないため）。  
任意指定: 環境変数 `DEM_DOWNLOADER_PYTHON=/path/to/envs/dem_downloader/bin/python`

## ディレクトリ

```
projects/<名>/project.yaml   共通設定・各ツールの引数
projects/<名>/work/          DEM・グリッド VRT など
projects/<名>/output/        ツール出力
projects/<名>/logs/          実行ログ
```

## テスト

```bash
python -m pytest -q tests
```

## グリッド・出力

- **グリッド間隔 (m)** を空欄にすると、NISAR RSLC のスワス中央における **地上方位・地上距離ピクセル間隔の細い方** を自動使用します（`isce3.product.get_radar_grid_nominal_ground_spacing`）。
- **出力ファイル名**（geocode / rtc）を指定すると `projects/<名>/output/` にその名前で保存します（空欄は従来の既定名）。パス区切りは不可です。

## geocode（NISAR）

geocode タブの **「ジオコードの種類」** で選べます。

| モード | 内容 |
|--------|------|
| **地図上の γ⁰（RTC 付き・既定）** | `plant_isce3_geocode`（area-projection RTC） |
| **複素 GSLC** | `isce3.geocode_slc`（位相保持・InSAR 向け、RTC なし） |

RTC 付きは geocode タブの plant オプション（`--output-terrain-radiometry` 等）で上書き可能です。

## 今後の拡張

- 地図 ROI（ccd3 と同等の描画・中心+km）
- ツール追加（info, topo, polygon…）
- geocode の plant オプションと NISAR ラッパーの切り替え UI