# Utilities

リモートセンシング・写真測量まわりで使う小さなツール群です。

| プロジェクト | 概要 |
|-------------|------|
| [dem_downloader](./dem_downloader/) | 国土地理院 DEM タイルから ROI の GeoTIFF を組み立てるブラウザ GUI |
| [plant-isce3/ccd3](./plant-isce3/ccd3/) | plant-isce3 による三時期 CCD（Sentinel-1 / NISAR RSLC、ブラウザ GUI） |
| [kp_DIM](./kp_DIM/) | [Deep Image Matching](https://github.com/3DOM-FBK/deep-image-matching) のローカル作業用コピー（Tk ランチャー付き） |

## クイックリンク

- **DEM ダウンローダ**: `conda activate dem_downloader` → `python run_dem_gui.py`（[README](./dem_downloader/README.md)）
- **三時期 CCD**: `conda activate plant-isce3` → `python plant-isce3/ccd3/run_ccd3_gui.py`（[README](./plant-isce3/ccd3/README.md)）
- **DIM**: `kp_DIM/deep-image-matching` を参照（[README](./kp_DIM/README.md)）

## ライセンス

各サブディレクトリの上流プロジェクト・データのライセンスに従ってください。`dem_downloader` の DEM データは [国土地理院](https://maps.gsi.go.jp/development/ichiran.html) の利用規約に従います。
