from energy.sources.beestat import parse_summaries

ROW = {"runtime_thermostat_summary_id": 47512207, "user_id": 28979, "thermostat_id": 47722,
       "date": "2022-11-25", "count": 288, "sum_compressor_cool_1": 0, "sum_compressor_cool_2": 0,
       "sum_compressor_heat_1": 0, "sum_compressor_heat_2": 0, "sum_auxiliary_heat_1": 7140,
       "sum_auxiliary_heat_2": 0, "sum_fan": 7770, "sum_humidifier": 0, "sum_dehumidifier": 0,
       "sum_ventilator": 0, "sum_economizer": 0, "sum_heating_degree_days": 25.4,
       "sum_cooling_degree_days": 0, "avg_outdoor_temperature": 39.6, "avg_outdoor_humidity": 66,
       "min_outdoor_temperature": 33.1, "max_outdoor_temperature": 47.4,
       "avg_indoor_temperature": 63.1, "avg_indoor_humidity": 44, "deleted": False}


def test_parse_summaries_maps_fields():
    [r] = parse_summaries([ROW])
    assert r["thermostat_id"] == 47722 and r["date"] == "2022-11-25"
    assert r["aux_heat_s"] == 7140 and r["fan_s"] == 7770 and r["avg_outdoor_f"] == 39.6
    assert r["comp_heat_s"] == 0 and r["interval_count"] == 288 and r["source"] == "beestat"
    assert r["min_outdoor_f"] == 33.1 and r["max_outdoor_f"] == 47.4 and r["avg_indoor_f"] == 63.1


def test_parse_summaries_skips_deleted():
    assert parse_summaries([{**ROW, "deleted": True}]) == []
