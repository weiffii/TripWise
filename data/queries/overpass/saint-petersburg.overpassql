[out:json][timeout:25];
rel["type"="boundary"]["boundary"="administrative"]["admin_level"="4"]["ISO3166-2"="RU-SPE"]->.city;
.city out tags;
.city map_to_area ->.searchArea;
nwr(area.searchArea)["tourism"~"^(museum|gallery)$"]["name"]; out center meta 12;
nwr(area.searchArea)["leisure"~"^(park|garden|nature_reserve)$"]["name"]; out center meta 12;
nwr(area.searchArea)["amenity"="cafe"]["name"]; out center meta 12;
nwr(area.searchArea)["tourism"="viewpoint"]["name"]; out center meta 12;
