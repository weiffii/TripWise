[out:json][timeout:35];
rel["boundary"="administrative"]["ISO3166-2"="RU-MOW"]->.city;
.city map_to_area->.moscow;
(
  rel(area.moscow)["type"="route"]["route"="subway"];
  rel(area.moscow)["type"="route"]["route"~"^(train|light_rail)$"]["ref"~"^(14|МЦК|[DД][1-4][AА]?)$"];
)->.rail;
.rail out body;
(
  node(r.rail:"stop");
  node(r.rail:"stop_entry_only");
  node(r.rail:"stop_exit_only");
);
out body;
