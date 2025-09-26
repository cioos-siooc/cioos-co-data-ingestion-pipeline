import pprint
from marine_buoy_parser import Marine_buoy_parser

def test_file(parser,file):
    return parser.parse_marine_xml(file)
    
if __name__ == "__main__":
    parser=Marine_buoy_parser()
    data = test_file(parser,'data/marine_buoys/2025-09-16-1240-4400488-AUTO-swob.xml')
    pprint.pprint(data, indent=1, sort_dicts=False) 
    #parser.createMappingFile()
    #parser.toCSV()
    parser.dirTOCSV('/home/richard/workspaces/ecccbuoys2/data/marine_buoys')
    #parser.updateMappingFile()
    #print(parser.data['metadata']['wmo_synop_id']['value'])
    #parser.toCSV()
