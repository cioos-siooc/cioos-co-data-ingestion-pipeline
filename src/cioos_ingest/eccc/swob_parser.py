#!/usr/bin/env python3
"""
Sarracenia callback plugin to parse marine buoy XML files
Extracts key oceanographic data and saves as JSON and CSV
"""
import os
import json
import csv
import io
import xml.etree.ElementTree as ET
import logging

logger = logging.getLogger(__name__)

# SWOB identification elements written as leading columns (date_tm first).
METADATA_FIELDS = ['date_tm', 'lat', 'long', 'stn_typ', 'wmo_synop_id', 'wmo_id_extnd',
                   'stn_nam', 'msc_id', 'stn_elev', 'rpt_typ', 'logr_typ']


def validate_nccsv_header(path):
    """Check that a station NCCSV file has a complete metadata header.

    ERDDAP rejects a file whose columns lack units/*DATA_TYPE* rows, and once
    a bad header is written every append compounds the loss (issue #5), so
    this is checked after header creation and again before publishing.
    Returns (True, "") or (False, reason).
    """
    with open(path, 'r', newline='') as fh:
        reader = csv.reader(fh)
        meta_rows = []
        columns = None
        for row in reader:
            if not row:
                continue
            if row[0] == '*END_METADATA*':
                columns = next(reader, None)
                break
            meta_rows.append(row)
        else:
            return False, "no *END_METADATA* row"
    if not columns:
        return False, "no column-name row after *END_METADATA*"
    with_units = {r[0] for r in meta_rows if len(r) >= 3 and r[1] == 'units'}
    with_dtype = {r[0] for r in meta_rows if len(r) >= 3 and r[1] == '*DATA_TYPE*'}
    missing = [c for c in columns if c not in with_units or c not in with_dtype]
    if missing:
        return False, f"columns missing units/*DATA_TYPE* rows: {', '.join(missing)}"
    if 'time' not in columns:
        return False, "no 'time' column (date_tm rename not applied)"
    return True, ""


class Marine_buoy_parser:
 
    
    def __init__(self):
        self.data={}
        # NCCSV is staged locally; the consumer publishes each touched station
        # file to the PUBLISH_URL destination (see cioos_ingest.publish).
        data_dir = os.environ.get("ECCC_DATA_DIR", "data")
        config_dir = os.environ.get("ECCC_CONFIG_DIR", "config")
        self.csvFolder = os.path.join(data_dir, "nccsv")
        self.mappingFile = os.path.join(config_dir, "ECCCbuoys_json_fields.json")
        self.typesFile = os.path.join(config_dir, "ECCCbuoys_types.json")
    
    def getBuoyId(self):
        return self.data['metadata']['wmo_synop_id']['value']
    def updateMappingFile(self):
        """Return this station's mapping entry, extending the persisted mapping.

        A new station gets every field of its first message. A known station
        gains metadata fields added to METADATA_FIELDS and observation fields
        it starts reporting later (a sensor that was MSNG-free, or absent, in
        its first message); untyped late fields are dropped with an error
        rather than breaking a station that already publishes.
        """
        mapping = self.getMapping()
        wmo_synop_id = self.getBuoyId()
        if wmo_synop_id not in mapping:
            mapping[wmo_synop_id] = {
                "metadata": list(METADATA_FIELDS),
                "observations": list(self.data["observations"].keys()),
            }
            logger.info(f"added station {wmo_synop_id} to {self.mappingFile}")
        else:
            entry = mapping[wmo_synop_id]
            types = self.getTypes()
            added = [f for f in METADATA_FIELDS if f not in entry["metadata"]]
            entry["metadata"] += added
            for field in self.data["observations"]:
                if field in entry["observations"]:
                    continue
                if field not in types:
                    logger.error(f"station {wmo_synop_id}: observation field '{field}' "
                                 f"has no entry in {self.typesFile}; dropping it")
                    continue
                entry["observations"].append(field)
                added.append(field)
            if not added:
                return entry
            logger.info(f"station {wmo_synop_id}: added fields {added} to {self.mappingFile}")
        os.makedirs(os.path.dirname(self.mappingFile), exist_ok=True)
        with open(self.mappingFile, 'w') as f:
            json.dump(mapping, f, indent=4)
        return mapping[wmo_synop_id]
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
    def getFieldType(self, types, field):
        """Resolve a field's (fieldname, unit, type) from the curated types file.

        Raises if the field has no usable entry: a header written without a
        field's units/*DATA_TYPE* rows is unreadable by ERDDAP forever, so we
        refuse to write the station file at all (see issue #5).
        """
        if field not in types:
            raise RuntimeError(
                f"field '{field}' has no entry in {self.typesFile}; "
                "add it to the curated types file before this station can be written")
        entry = types[field]
        if 'unit' not in entry or 'type' not in entry:
            raise RuntimeError(
                f"field '{field}' in {self.typesFile} is missing 'unit' or 'type'")
        return entry.get('rename', field), entry['unit'], entry['type']

    def buildHeader(self, entry):
        """Return (text, fields): the NCCSV metadata block through the column-name
        row that a station file with this mapping entry must start with."""
        types = self.getTypes()
        fields =  []
        units = []
        for field in entry["metadata"]:
            fieldname, unit, fieldtype = self.getFieldType(types, field)
            fields.append(fieldname)
            units.append(f"{fieldname},units,{unit}")
            units.append(f"{fieldname},*DATA_TYPE*,{fieldtype}")
        for field in entry["observations"]:
            fieldname, unit, fieldtype = self.getFieldType(types, field)
            fields.append(fieldname)
            fields.append(fieldname+"_qa_summary")
            fields.append(fieldname+"_data_flag")
            units.append(f"{fieldname},units,{unit}")
            units.append(f"{fieldname},*DATA_TYPE*,{fieldtype}")
            units.append(f"{fieldname}_qa_summary,units,unitless")
            units.append(f"{fieldname}_qa_summary,*DATA_TYPE*,int")
            units.append(f"{fieldname}_data_flag,units,unitless")
            units.append(f"{fieldname}_data_flag,*DATA_TYPE*,int")
        csvFile = io.StringIO()
        #csvFile.write('*GLOBAL*,Conventions,"..., NCCSV-..."\r\n')
        csvFile.write('*GLOBAL*,Conventions,"COARDS, CF-1.6, ACDD-1.3, NCCSV-1.2"\r\n')
        csvFile.write('*GLOBAL*,featureType,TimeSeries\r\n')
        csvFile.write('*GLOBAL*,cdm_data_type,TimeSeries\r\n')
        csvFile.write('*GLOBAL*,cdm_timeseries_variables,stn_nam\r\n')
        csvFile.write('*GLOBAL*,contributor_name,ECCC\r\n')
        csvFile.write('*GLOBAL*,contributor_role,owner \r\n')
        csvFile.write('*GLOBAL*,creator_email,ec.dps-client.ec@canada.ca\r\n')
        csvFile.write('*GLOBAL*,creator_name,ECCC\r\n')
        csvFile.write('*GLOBAL*,creator_type,institution\r\n')
        csvFile.write('*GLOBAL*,creator_url,https://eccc-msc.github.io/open-data/\r\n')
        csvFile.write('*GLOBAL*,title,Realtime data from Environment and Climate Change Canada buoys (Meteorological Service of Canada)\r\n')
        csvFile.write('*GLOBAL*,infoUrl,https://eccc-msc.github.io/open-data/msc-data/readme_en/ \r\n')
        csvFile.write('*GLOBAL*,institution,ECCC\r\n')
        csvFile.write('*GLOBAL*,keywords,"air, americal, amt, average, avg_air_temp_pst10mts, avg_air_temp_pst10mts_1, avg_air_temp_pst10mts_1_data_flag, avg_air_temp_pst10mts_1_qa_summary, avg_air_temp_pst10mts_data_flag, avg_air_temp_pst10mts_qa_summary, avg_batry_volt_pst10mts, avg_batry_volt_pst10mts_1, avg_batry_volt_pst10mts_1_data_flag, avg_batry_volt_pst10mts_1_qa_summary, avg_batry_volt_pst10mts_data_flag, avg_batry_volt_pst10mts_qa_summary, avg_crnt_volt_pst10mts, avg_crnt_volt_pst10mts_1, avg_max_wave_hgt_pst20mts, avg_max_wave_hgt_pst20mts_1, avg_max_wave_hgt_pst20mts_1_data_flag, avg_max_wave_hgt_pst20mts_1_qa_summary, avg_max_wave_hgt_pst20mts_data_flag, avg_max_wave_hgt_pst20mts_qa_summary, avg_max_wave_pd_pst20mts, avg_max_wave_pd_pst20mts_1, avg_max_wave_pd_pst20mts_1_data_flag, avg_max_wave_pd_pst20mts_1_qa_summary, avg_max_wave_pd_pst20mts_data_flag, avg_max_wave_pd_pst20mts_qa_summary, avg_mslp_pst10mts, avg_mslp_pst10mts_1, avg_mslp_pst10mts_1_data_flag, avg_mslp_pst10mts_1_qa_summary, avg_mslp_pst10mts_data_flag, avg_mslp_pst10mts_qa_summary, avg_sea_sfc_temp_pst10mts, avg_sea_sfc_temp_pst10mts_1, avg_sea_sfc_temp_pst10mts_1_data_flag, avg_sea_sfc_temp_pst10mts_1_qa_summary, avg_sea_sfc_temp_pst10mts_data_flag, avg_sea_sfc_temp_pst10mts_qa_summary, avg_solr_panl_crnt_pst10mts, avg_solr_panl_crnt_pst10mts_1, avg_solr_panl_crnt_pst10mts_1_data_flag, avg_solr_panl_crnt_pst10mts_1_qa_summary, avg_solr_panl_crnt_pst10mts_data_flag, avg_solr_panl_crnt_pst10mts_qa_summary, avg_stn_pres_pst10mts, avg_stn_pres_pst10mts_1, avg_stn_pres_pst10mts_1_data_flag, avg_stn_pres_pst10mts_1_qa_summary, avg_stn_pres_pst10mts_2, avg_stn_pres_pst10mts_2_data_flag, avg_stn_pres_pst10mts_2_qa_summary, avg_stn_pres_pst10mts_data_flag, avg_stn_pres_pst10mts_qa_summary, avg_wave_hgt_pst20mts, avg_wave_hgt_pst20mts_1, avg_wave_hgt_pst20mts_1_data_flag, avg_wave_hgt_pst20mts_1_qa_summary, avg_wave_hgt_pst20mts_data_flag, avg_wave_hgt_pst20mts_qa_summary, avg_wave_pd_pst20mts, avg_wave_pd_pst20mts_1, avg_wave_pd_pst20mts_1_data_flag, avg_wave_pd_pst20mts_1_qa_summary, avg_wave_pd_pst20mts_data_flag, avg_wave_pd_pst20mts_qa_summary, avg_wnd_dir_pst10mts, avg_wnd_dir_pst10mts_1, avg_wnd_dir_pst10mts_1_data_flag, avg_wnd_dir_pst10mts_1_qa_summary, avg_wnd_dir_pst10mts_2, avg_wnd_dir_pst10mts_2_data_flag, avg_wnd_dir_pst10mts_2_qa_summary, avg_wnd_dir_pst10mts_data_flag, avg_wnd_dir_pst10mts_qa_summary, avg_wnd_spd_pst10mts, avg_wnd_spd_pst10mts_1, avg_wnd_spd_pst10mts_1_data_flag, avg_wnd_spd_pst10mts_1_qa_summary, avg_wnd_spd_pst10mts_2, avg_wnd_spd_pst10mts_2_data_flag, avg_wnd_spd_pst10mts_2_qa_summary, avg_wnd_spd_pst10mts_data_flag, avg_wnd_spd_pst10mts_qa_summary, avg_wtr_lvl_snsr_volt_pst10mts, avg_wtr_lvl_snsr_volt_pst10mts_1, avg_wtr_lvl_snsr_volt_pst10mts_1_data_flag, avg_wtr_lvl_snsr_volt_pst10mts_1_qa_summary, avg_wtr_lvl_snsr_volt_pst10mts_data_flag, avg_wtr_lvl_snsr_volt_pst10mts_qa_summary, batry, buoy, stn_typ, char, crnt, crnt_buoy_lat, crnt_buoy_lat_data_flag, crnt_buoy_lat_qa_summary, crnt_buoy_long, crnt_buoy_long_data_flag, crnt_buoy_long_qa_summary, currents, data, datamart, date, time, dir, disp, elev, extended, flag, hgt, identifier, lat_data_flag, lat_qa_summary, lat, longitude, long_data_flag, long_qa_summary, long, lvl, max, max_avg_wnd_spd_pst10mts, max_avg_wnd_spd_pst10mts_1, max_avg_wnd_spd_pst10mts_1_data_flag, max_avg_wnd_spd_pst10mts_1_qa_summary, max_avg_wnd_spd_pst10mts_2, max_avg_wnd_spd_pst10mts_2_data_flag, max_avg_wnd_spd_pst10mts_2_qa_summary, max_avg_wnd_spd_pst10mts_data_flag, max_avg_wnd_spd_pst10mts_qa_summary, meteorological, model, moored, msc, msc_id, msc_id_data_flag, msc_id_qa_summary, mslp, nam, north, number, organisation, panl, pk_wave_hgt_pst20mts, pk_wave_hgt_pst20mts_1, pk_wave_hgt_pst20mts_1_data_flag, pk_wave_hgt_pst20mts_1_qa_summary, pk_wave_hgt_pst20mts_data_flag, pk_wave_hgt_pst20mts_qa_summary, pk_wave_pd_pst20mts, pk_wave_pd_pst20mts_1, pk_wave_pd_pst20mts_1_data_flag, pk_wave_pd_pst20mts_1_qa_summary, pk_wave_pd_pst20mts_data_flag, pk_wave_pd_pst20mts_qa_summary, pres, pres_tend_amt_pst3hrs, pres_tend_amt_pst3hrs_1, pres_tend_amt_pst3hrs_1_data_flag, pres_tend_amt_pst3hrs_1_qa_summary, pres_tend_amt_pst3hrs_data_flag, pres_tend_amt_pst3hrs_qa_summary, pres_tend_char_pst3hrs, pres_tend_char_pst3hrs_data_flag, pres_tend_char_pst3hrs_qa_summary, pst10mts, pst20mts, pst3hrs, quality, realtime, result, result_time, rpt, rpt_typ, sampling, sea, senx, sfc, sig, sig_wave_hgt_pst20mts, sig_wave_hgt_pst20mts_1, sig_wave_hgt_pst20mts_1_data_flag, sig_wave_hgt_pst20mts_1_qa_summary, sig_wave_hgt_pst20mts_data_flag, sig_wave_hgt_pst20mts_qa_summary, sig_wave_pd_pst20mts, sig_wave_pd_pst20mts_1, sig_wave_pd_pst20mts_1_data_flag, sig_wave_pd_pst20mts_1_qa_summary, sig_wave_pd_pst20mts_data_flag, sig_wave_pd_pst20mts_qa_summary, snsr, solr, spd, statistics, stn, stn_elev, stn_elev_data_flag, stn_elev_qa_summary, stn_nam,  summary, surface, surface waves, synop, table, temperature, tend, time, typ, vert, volt, wave, waves, wmo, wmo_id_extnd, wmo_id_extnd_data_flag, wmo_id_extnd_qa_summary, wmo_synop_id, wnd, wnd_snsr_vert_disp, wnd_snsr_vert_disp_1, wnd_snsr_vert_disp_1_data_flag, wnd_snsr_vert_disp_1_qa_summary, wnd_snsr_vert_disp_2, wnd_snsr_vert_disp_2_data_flag, wnd_snsr_vert_disp_2_qa_summary, wnd_snsr_vert_disp_data_flag, wnd_snsr_vert_disp_qa_summary, world, wtr"\r\n')
        csvFile.write('*GLOBAL*,license,Open Government Licence - Canada  https://open.canada.ca/en/open-government-licence-canada\r\n')
        csvFile.write('*GLOBAL*,publisher_email,ec.dps-client.ec@canada.ca\r\n')
        csvFile.write('*GLOBAL*,publisher_institution,ECCC\r\n')
        csvFile.write('*GLOBAL*,publisher_name,ECCC\r\n')
        csvFile.write('*GLOBAL*,publisher_url,https://eccc-msc.github.io/open-data/\r\n')
        csvFile.write('*GLOBAL*,platform_vocabulary,https://vocab.nerc.ac.uk/collection/L06/current/\r\n')
        csvFile.write('*GLOBAL*,platform,moored surface buoy\r\n')
        csvFile.write('*GLOBAL*,standard_name_vocabulary,CF Standard Name Table v79\r\n')
        csvFile.write('*GLOBAL*,subsetVariables,"wmo_synop_id, stn_typ,longitude, latitude, wmo_id_extnd,stn_nam, msc_id, stn_elev,rpt_typ"\r\n')
        csvFile.write('*GLOBAL*,defaultDataQuery,"time,stn_typ,wmo_synop_id,wmo_id_extnd,stn_nam,msc_id,stn_elev,latitude,longitude,time,rpt_typ,crnt_buoy_lat,crnt_buoy_long,avg_crnt_volt_pst10mts,avg_crnt_volt_pst10mts_1,avg_solr_panl_crnt_pst10mts,avg_solr_panl_crnt_pst10mts_1,avg_batry_volt_pst10mts,avg_batry_volt_pst10mts_1,avg_air_temp_pst10mts,avg_air_temp_pst10mts_1,avg_stn_pres_pst10mts,avg_stn_pres_pst10mts_1,avg_stn_pres_pst10mts_2,avg_sea_sfc_temp_pst10mts,avg_sea_sfc_temp_pst10mts_1,avg_wnd_spd_pst10mts,avg_wnd_spd_pst10mts_1,avg_wnd_spd_pst10mts_2,avg_wnd_dir_pst10mts,avg_wnd_dir_pst10mts_1,avg_wnd_dir_pst10mts_2,max_avg_wnd_spd_pst10mts,max_avg_wnd_spd_pst10mts_1,max_avg_wnd_spd_pst10mts_2,wnd_snsr_vert_disp,wnd_snsr_vert_disp_1,wnd_snsr_vert_disp_2,pk_wave_pd_pst20mts,pk_wave_pd_pst20mts_1,pk_wave_hgt_pst20mts,pk_wave_hgt_pst20mts_1,sig_wave_pd_pst20mts,sig_wave_pd_pst20mts_1,sig_wave_hgt_pst20mts,sig_wave_hgt_pst20mts_1,avg_wave_pd_pst20mts,avg_wave_pd_pst20mts_1,avg_wave_hgt_pst20mts,avg_wave_hgt_pst20mts_1,avg_max_wave_pd_pst20mts,avg_max_wave_pd_pst20mts_1,avg_max_wave_hgt_pst20mts,avg_max_wave_hgt_pst20mts_1,avg_mslp_pst10mts,avg_mslp_pst10mts_1,avg_wtr_lvl_snsr_volt_pst10mts,avg_wtr_lvl_snsr_volt_pst10mts_1,pres_tend_amt_pst3hrs,pres_tend_amt_pst3hrs_1,pres_tend_char_pst3hrs,compass_1,compass_2,source,avg_cmpss_hdng_pst10mts_1,avg_cmpss_hdng_pst10mts_2,avg_cmpss_hdng_pst10mts,avg_obstrn_lamp_crnt_pst10mts,avg_sig_wave_hgt_pst20mts,avg_sig_wave_pd_pst20mts,avg_spetrl_wave_pd_pst20mts,avg_wave_dir_pst20mts,avg_wave_dir_sprd_pst20mts,bad_wnd_smpls_1,bad_wnd_smpls_2,max_wave_crst_hgt_abv_avg_wtr_lvl_pst20mts,max_wnd_spd_pst10mts_1,max_wnd_spd_pst10mts_2,max_wnd_spd_pst10mts,pd_of_max_wave_hgt_pst20mts,pk_wave_dir_sprd_pst20mts,spetrl_sig_wave_hgt_pst20mts,spetrl_wave_enrgy_pd_pst20mts,wtchmn_boot_cnt_pst1hr,wmo_id_extnd,logr_typ,max_wave_hgt_pst20mts,avg_pk_wave_dir_pst20mts,nesdis_id,pk_wave_pd_pst35mts_10mts_ago,sig_wave_hgt_pst35mts_10mts_ago,pk_wave_hgt_pst35mts_10mts_ago,batry_volt&time>now-1day"\r\n')
        
        csvFile.write('*GLOBAL*,summary,Surface weather and marine observations from the Meteorological Service of Canada\'s open data system.\r\n')
        csvFile.write('stn_nam,cf_role,timeseries_id\r\n')

        for unit in units:
            csvFile.write(unit +'\r\n')
        csvFile.write('*END_METADATA*\r\n')
        csv.writer(csvFile).writerow(fields)
        return csvFile.getvalue(), fields
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
        # The types file is curated by hand (it carries the date_tm->time,
        # lat->latitude, long->longitude renames the ERDDAP dataset fragment
        # depends on); it can never be reconstructed from live SWOB metadata,
        # so its absence is a fatal configuration error (issue #5).
        if not os.path.isfile(self.typesFile):
            raise RuntimeError(
                f"types file {self.typesFile} is missing; the curated "
                "ECCCbuoys_types.json must be deployed before parsing")
        with open(self.typesFile, 'r') as f:
            types = json.load(f)
        if not types:
            raise RuntimeError(f"types file {self.typesFile} is empty")
        return types


    def toCSV(self):
        """Append the parsed observation to its station NCCSV file.

        The file header is derived from the mapping and types file; when it no
        longer matches (new fields, changed types) the file is rewritten with
        the current header and existing rows re-laid-out by column name. An
        observation whose time is already in the file is not appended again
        (the AMQP feed can re-announce the same SWOB file).
        """
        entry = self.updateMappingFile()
        wmo_synop_id = self.getBuoyId()
        csv_path = os.path.join(self.csvFolder, wmo_synop_id + ".csv")
        header, fields = self.buildHeader(entry)

        rows = []
        if os.path.isfile(csv_path):
            with open(csv_path, newline='') as fh:
                content = fh.read()
            if not content.startswith(header):
                rows = self._dataRows(content)
                seen = set()
                rows = [r for r in rows
                        if not (r.get('time') in seen or seen.add(r.get('time')))]
                self._writeCSV(csv_path, header, fields, rows)
                logger.info(f"rewrote {csv_path} with regenerated header")
            else:
                rows = self._dataRows(content)
        else:
            self._writeCSV(csv_path, header, fields, [])
            ok, reason = validate_nccsv_header(csv_path)
            if not ok:
                os.remove(csv_path)
                raise RuntimeError(f"wrote invalid NCCSV header for {csv_path}, removed it: {reason}")

        data = []
        for field in entry["metadata"]:
            try:
                value = self.data['metadata'][field]['value']
            except KeyError:
                logger.warning(f"metadata field '{field}' not found in metadata")
                value = ''
            data.append('' if value == 'MSNG' else value)

        for field in entry["observations"]:
            try:
                if self.data["observations"][field]['value'] == 'MSNG':
                    data.append('')
                else:
                    data.append(self.data["observations"][field]['value'])
            except KeyError:
                #TODO handle this case and add sentry call
                logger.warning(f"observation field '{field}' not found in data")
                data.append('')
            try:
                data.append(self.data["observations"][field]['qualifiers']['qa_summary']['value'])
            except KeyError:
                logger.warning(f"qualifier 'qa_summary' not found in '{field}'")
                data.append('')
            try:
                data.append(self.data["observations"][field]['qualifiers']['data_flag']['value'])
            except KeyError:
                logger.warning(f"qualifier 'data_flag' not found in '{field}'")
                data.append('')

        time_value = data[fields.index('time')]
        if any(row.get('time') == time_value for row in rows):
            logger.warning(f"{csv_path}: observation at {time_value} already present; not appending")
            return csv_path
        with open(csv_path, 'a', newline='') as fh:
            csv.writer(fh).writerow(data)
        return csv_path

    @staticmethod
    def _dataRows(content):
        """Data rows of an NCCSV file, as dicts keyed by column name."""
        _, _, body = content.partition('*END_METADATA*\r\n')
        reader = csv.DictReader(io.StringIO(body, newline=''))
        return list(reader)

    @staticmethod
    def _writeCSV(csv_path, header, fields, rows):
        """Atomically write header plus rows (missing columns left empty)."""
        os.makedirs(os.path.dirname(csv_path), exist_ok=True)
        tmp = csv_path + '.tmp'
        with open(tmp, 'w', newline='') as fh:
            fh.write(header)
            writer = csv.writer(fh)
            for row in rows:
                writer.writerow([row.get(f) or '' for f in fields])
        os.replace(tmp, csv_path)

    

    def dirTOCSV(self, directory):
        logger.info(f"Processing directory: {directory}")
        for file in os.listdir(directory):
            full_path = os.path.join(directory, file)
            if os.path.isfile(full_path):
                logger.info(f"Processing file: {file}")
                data = self.parse_marine_xml(full_path)
                if data:
                    self.toCSV()
                else:
                    logger.error(f"Failed to parse file: {file}")
            else:
                logger.info(f"Skipping non-file entry: {file}")


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
        except ET.ParseError:
            logger.exception(f"Error parsing XML file: {file_path}")
            return None
        except FileNotFoundError:
            logger.exception(f"File not found: {file_path}")
            return None
        except Exception:
            logger.exception(f"Unexpected error parsing {file_path}")
            return None


        

