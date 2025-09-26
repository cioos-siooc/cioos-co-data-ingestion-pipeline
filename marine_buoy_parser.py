#!/usr/bin/env python3
"""
Sarracenia callback plugin to parse marine buoy XML files
Extracts key oceanographic data and saves as JSON and CSV
"""
import os
import json
import csv
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime
import logging
import re
import traceback
import pprint

logger = logging.getLogger(__name__)


class Marine_buoy_parser:
 
    
    def __init__(self):
        self.data={}
        self.csvFolder = "./datasets/ECCCbuoys/"
        self.mappingFile ="./config/ECCCbuoys_json_fields.json"
        self.typesFile = "./config/ECCCbuoys_types.json"
    
    def getBuoyId(self):
        return self.data['metadata']['wmo_synop_id']['value']
    def updateMappingFile(self):
        if not os.path.isfile(self.mappingFile):
            mapping={}
        else:
            with open(self.mappingFile, 'r') as f:
                mapping = json.load(f)
        wmo_synop_id = self.data['metadata']['wmo_synop_id']['value'] 
        #need to handle if new fields are added. 
        if wmo_synop_id not in mapping:
            mapping[wmo_synop_id] = {}
            mapping[wmo_synop_id]["metadata"] = ['date_tm', 'lat', 'long', 'stn_typ', 'wmo_synop_id', 'wmo_id_extnd', 'stn_nam', 'msc_id', 'stn_elev']
            mapping[wmo_synop_id]["observations"]=list(self.data["observations"].keys())
        
        print(mapping)
        # Ensure directory exists
        os.makedirs(os.path.dirname(self.mappingFile), exist_ok=True)
        with open(self.mappingFile, 'w') as f:
            json.dump(mapping, f, indent=4)
        return mapping
    """
    def updateMappingFile(self, new_field):
        print(f'Adding new field to mapping: {new_field}')
        mapping={}
        with open(self.mappingFile, 'r') as f:
            mapping = json.load(f)
        mapping["observations"].append(new_field)
        print(mapping)  
        with open(self.mappingFile, 'w') as f:
            json.dump(mapping, f, indent=1)
    
    """
    def addFieldType(self, field, fieldtype="float", unit="unitless"):
        os.makedirs(os.path.dirname(self.typesFile), exist_ok=True)
        if not os.path.isfile(self.typesFile):
            types={}
        else:
            with open(self.typesFile, 'r') as f:
                types = json.load(f)
        types[field] = {}
        types[field]['type']=fieldtype
        types[field]['unit']=unit
        print(types)
        with open(self.typesFile, 'w') as f:
            json.dump(types, f, indent=4)
        
    def createCSVHeader(self, write = True):
        types = self.getTypes()
        # Ensure directory exists
        if not os.path.isfile(self.mappingFile):
            self.createMappingFile()
        
        with open(self.mappingFile, 'r') as f:
            mapping = json.load(f)
        wmo_synop_id = self.getBuoyId()
        fields =  [] 
        units = []
        units.append("date_tm,standard_name,time")



        #mapping[wmo_synop_id]["metadata"].copy()
        for field in mapping [wmo_synop_id]["metadata"]:
            fields.append(field)
            print(field)

            try:
                if field not in types:
                    print("adding field")
                    self.addFieldType(field, unit = self.data['metadata'][field]['uom'])
                units.append(f"{field},units,{types[field]['unit']}")
                units.append(f"{field},*DATA_TYPE*,{types[field]['type']}")

            except:
                print(f"Warning: metadata field '{field}' not found in metadata")
                continue
        for field in mapping[wmo_synop_id]["observations"]:
            fields.append(field)
            fields.append(field+"_qa_summary")
            fields.append(field+"_data_flag")
            if field not in types:
                self.addFieldType(field, unit = self.data['observations'][field]['uom'])
                #self.addFieldType(field+"_qa_summary", fieldtype = "int", unit = "unitless")
                #self.addFieldType(field+"_data_flag", fieldtype = "int", unit = "unitless")
            try:
                units.append(f"{field},units,{types[field]['unit']}")
                units.append(f"{field},*DATA_TYPE*,{types[field]['type']}")
                units.append(f"{field}_qa_summary,units,unitless")
                units.append(f"{field}_qa_summary,*DATA_TYPE*,int")
                units.append(f"{field}_data_flag,units,unitless")
                units.append(f"{field}_data_flag,*DATA_TYPE*,int")
            except:
                print(f"Warning: data field '{field}' not found in data")
                continue
        if write:
            csvFile = wmo_synop_id + ".csv"
            csvFile = os.path.join(self.csvFolder, csvFile)
            os.makedirs(os.path.dirname(csvFile), exist_ok=True)
            with open(csvFile, 'w') as csvFile:
                csvFile.write('*GLOBAL*,Conventions,"..., NCCSV-..."\r\n')
                
                
                writer = csv.writer(csvFile)
                for unit in units:
                    csvFile.write(unit +'\r\n')
                csvFile.write('*END_METADATA*\r\n')
                writer.writerow(fields)
        return fields
    """
    def updateCSVHeader(self):
        print("Updating CSV header...")
        fields, units = self.createCSVHeader(write=False)
        with open(self.csvFile, 'r', newline='') as csvfile:
            reader = csv.reader(csvfile)
            existing_rows = list(reader)
        if existing_rows:
            # Update the header row
            existing_rows[0] = fields
            existing_rows[1] = units
        with open(self.csvFile, 'w', newline='') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerows(existing_rows)
    """
    def getMapping(self):
        if os.path.isfile(self.mappingFile):
            with open(self.mappingFile, 'r') as f:
                return json.load(f)
        else:
            return {}
            
    def getTypes(self):
        if os.path.isfile(self.typesFile):
            with open(self.typesFile, 'r') as f:
                return json.load(f)
        else:
            return {}
            
            
    def toCSV(self):
    
        mapping = self.getMapping()
        wmo_synop_id = self.data['metadata']['wmo_synop_id']['value']
        if wmo_synop_id not in mapping:
            mapping = self.updateMappingFile()
        csvFile = wmo_synop_id + ".csv"
        csvFile = os.path.join(self.csvFolder, csvFile)
        if not os.path.isfile(csvFile):
            self.createCSVHeader()
            
        for field in self.data["observations"]:
                if field not in mapping[wmo_synop_id]["observations"]:
                    #TODO  handle this case and updated csv header/add sentry call 
                    #TODO remove print and add logging
                    print(f"Warning: observation field '{field}' not found in mapping")
        data = []
        for field in mapping[wmo_synop_id]["metadata"]:
            try:
                data.append(self.data['metadata'][field]['value'])
            except KeyError:
                print(f"Warning: metadata field '{field}' not found in metadata")
                data.append('')

        for field in mapping[wmo_synop_id]["observations"]:
            try:
                if self.data["observations"][field]['value'] == 'MSNG':
                    data.append('')
                else: 
                    data.append(self.data["observations"][field]['value'])
            except KeyError:
                #TODO handle this case and add sentry call
                #TODO remove prints and add logging
                print(f"Warning: observation field '{field}' not found in data")
                data.append('')
            try:     
                data.append(self.data["observations"][field]['qualifiers']['qa_summary']['value'])
            except KeyError:
                print(f"Warning: qualifier 'qa_summary' not found in '{field}'")
                data.append('')
            try:
                data.append(self.data["observations"][field]['qualifiers']['data_flag']['value'])
            except KeyError:
                print(f"Warning: qualifier 'data_flag' not found in '{field}'")
                data.append('')
                
        with open(csvFile, 'a') as csvFile:
            writer = csv.writer(csvFile)
            writer.writerow(data)
        """

        if not os.path.exists(self.csvFile):
            self.createCSVHeader()
        pass


        with open(self.mappingFile, 'r') as f:
            mapping = json.load(f)
            for field in self.data["observations"]:
                missingfield = False
                if field not in mapping["observations"]:
                    print(f"Warning: observation field '{field}' not found in mapping")
                    self.updateMappingFile(field)
                    missingfield = True
            if missingfield:
                self.updateCSVHeader()
            data = []
            for field in mapping["metadata"]:
                data.append(self.data['metadata'][field]['value'])
            for field in mapping["observations"]:
                try:
                    data.append(self.data["observations"][field]['value'])
                except KeyError:
                    print(f"Warning: observation field '{field}' not found in data")
                    data.append('MSNG')  # or use '' or any default value
        with open(self.csvFile, 'a', newline='') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(data)
        """

    

    def dirTOCSV(self, directory):
        print(f"Processing directory: {directory}")
        for file in os.listdir(directory):
            full_path = os.path.join(directory, file)
            if os.path.isfile(full_path):
                print(f"Processing file: {file}")
                data = self.parse_marine_xml(full_path)
                if data:
                    self.toCSV()
                else:
                    print(f"Failed to parse file: {file}")
            else:
                print(f"Skipping non-file entry: {file}")


    def parse_marine_xml(self, file_path):
        """
        Parse SWOB XML file and extract all observations.
        
        Args:
            filename (str): Path to the SWOB XML file
            
        Returns:
            dict: Dictionary containing metadata and observations
        """
        try:
            tree = ET.parse(file_path)
            root = tree.getroot()
            
            # Define namespaces
            namespaces = {
                'om': 'http://www.opengis.net/om/1.0',
                'gml': 'http://www.opengis.net/gml',
                'po': 'http://dms.ec.gc.ca/schema/point-observation/2.0'
            }
            
            # Extract metadata
            PO_NS = '{http://dms.ec.gc.ca/schema/point-observation/2.0}'
            OM_NS = '{http://www.opengis.net/om/1.0}'
            metadata_container = root.find('.//' + OM_NS + 'metadata/' + PO_NS + 'set/' + PO_NS + 'identification-elements')
            metadata = {}
            for elem in metadata_container.findall(PO_NS + 'element'):
                
                elem_name = elem.get('name')
                metadata[elem_name] = {}

                for name, value in elem.items():
                    if name == 'name':
                        continue
                    metadata[elem_name][name] = value          
            # Extract observations
            observations = {}
                        # Find the om:result/po:elements structure
            elements_container = root.find('.//om:result/po:elements', namespaces)
            for elem in elements_container.findall('po:element', namespaces):
                elem_name = elem.get('name')
                observations[elem_name] = {}
                for name, value in elem.items():
                    if name == 'name':
                        continue
                    observations[elem_name][name] = value
                

                qualifiers = elem.findall('qualifier')
                if not qualifiers:
                    qualifiers = elem.findall('po:qualifier', namespaces)
                
                if qualifiers:
                    observations[elem_name]['qualifiers'] = {}
                    for qualifier in qualifiers:
                        qualifier_name = qualifier.get('name')
                        observations[elem_name]['qualifiers'][qualifier_name] = {}
                        for name, value in qualifier.items():
                            if name == 'name':
                                continue
                            observations[elem_name]['qualifiers'][qualifier_name][name] = value
            self.data={"metadata": metadata, "observations": observations}
            #print(self.data)
            return self.data
        except ET.ParseError as e:
            traceback.print_exc()
            print(f"Error parsing XML file: {e}")
            return None
        except FileNotFoundError:
            traceback.print_exc()
            print(f"File not found: {filename}")
            return None
        except Exception as e:
            traceback.print_exc()            
            print(f"Unexpected error: {e}")
            return None


        

