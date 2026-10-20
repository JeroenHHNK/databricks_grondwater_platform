# -*- coding: utf-8 -*-
"""
Created on Thu Apr  3 17:02:40 2025

@author: thanzen
"""

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

excel_base_path = Path("D:/Python/Development/PhreaticLine/Beemster/Data")
grondwaterdata = excel_base_path / "waterstanden_beemster_03042025.xlsx"
data = pd.read_excel(grondwaterdata)


BE0049_BUKR = data["BE0049+00_BUKR_GMW_PB1_F-229"].to_numpy()
BE0049_BUKR = BE0049_BUKR / 100
BE0341_BIRK = data["BE0341+40_BIRK_GMW_PB1_F-497"].to_numpy()
BE0341_BUKR = data["BE0341+40_BUKR_GMW_PB1_F-272"].to_numpy()
time = data["Time"].to_numpy()


plt.plot(BE0049_BUKR, time)
