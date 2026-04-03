"""Constants for the FMA2 Undub Patcher."""

import os

SECTOR = 2048
DSI_BLOCK_SIZE = 0x40000

# CFC.DIG entry at directory offset 0x30 contains the XA track offset table
CFC_TRACK_TABLE_DIR_OFFSET = 0x30

# SCEI sound banks that differ between USA and JP (combat barks, voice SFX)
SCEI_BANK_INDICES = [7, 15, 34, 37, 49, 65, 293, 295, 297, 299,
                     309, 324, 334, 336, 341, 346, 373, 377]

# DSI cutscene names (MV00-MV11)
DSI_NAMES = ['MV00', 'MV01', 'MV02', 'MV03', 'MV04', 'MV05',
             'MV06', 'MV07', 'MV08', 'MV09', 'MV10', 'MV11']

EXPECTED_MD5 = {
    "usa": "2e79a69434561557dd0eaa9061d62eed",
    "jp":  "6804b82a9eb8d6a1e2d85a25683ec89d",
}

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUBS_DIR = os.path.join(SCRIPT_DIR, 'subs')
