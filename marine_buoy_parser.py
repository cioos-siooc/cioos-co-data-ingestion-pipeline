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
        self.csvFile = "./data/marine_buoys/csv/ECCCbuoys.csv"
        self.mappingFile ="./config/ECCCbuoys_json_fields.json"

    def createMappingFile(self):
        mapping={}
        mapping["metadata"]=['date_tm','wmo_id_extnd','stn_nam'] 
        mapping["observations"]=list(self.data["observations"].keys())
        print(mapping)
        # Ensure directory exists
        os.makedirs(os.path.dirname(self.mappingFile), exist_ok=True)
        with open(self.mappingFile, 'w') as f:
            json.dump(mapping, f, indent=4)
        return mapping
    
    def updateMappingFile(self, new_field):
        print(f'Adding new field to mapping: {new_field}')
        mapping={}
        with open(self.mappingFile, 'r') as f:
            mapping = json.load(f)
        mapping["observations"].append(new_field)
        print(mapping)  
        with open(self.mappingFile, 'w') as f:
            json.dump(mapping, f, indent=1)
    

    def createCSVHeader(self, write = True):
        # Ensure directory exists
        os.makedirs(os.path.dirname(self.csvFile), exist_ok=True)
        if not os.path.isfile(self.mappingFile):
            self.createMappingFile()
        
        with open(self.mappingFile, 'r') as f:
            mapping = json.load(f)

        fields = mapping["metadata"] + mapping["observations"]
        units = []
        for field in mapping["metadata"]:
            units.append(self.data['metadata'][field]['uom'])
        for field in mapping["observations"]:
            units.append(self.data["observations"][field]['uom'])
        if write:
            with open(self.csvFile, 'w', newline='') as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(fields)
                writer.writerow(units)
        return fields, units

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

    def toCSV(self):
        if not os.path.exists(os.path.dirname(self.mappingFile)):
            self.createMappingFile()

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


        

