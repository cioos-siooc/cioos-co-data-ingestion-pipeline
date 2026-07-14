"""
Manual driver for the ECCC SWOB-ML parser (not a pytest suite — there is no
committed XML fixture). Point it at a directory of downloaded SWOB XML files:

    uv run python tests/eccc_driver.py data/eccc/marine_buoys/xml
"""

import sys

from cioos_ingest.eccc.swob_parser import Marine_buoy_parser

if __name__ == "__main__":
    xml_dir = sys.argv[1] if len(sys.argv) > 1 else "./data/eccc/marine_buoys/xml"
    parser = Marine_buoy_parser()
    parser.dirTOCSV(xml_dir)
