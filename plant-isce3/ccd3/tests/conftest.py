import os
import sys

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_DIR)

from ccd3lib import envfix  # noqa: E402

envfix.apply()

# plant-isce3 同梱のテストデータ（NISAR RSLC 形式の ENVISAT データと一定高度 DEM）
PLANT_TEST_DATA = os.path.join(os.path.dirname(APP_DIR), "src", "plant-isce3", "tests", "data")
