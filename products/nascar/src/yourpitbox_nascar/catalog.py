TRACKS = [
    {"name": name, "type": kind} for kind, names in {
        "short": ["Bristol", "Bowman Gray", "Iowa", "Martinsville", "North Wilkesboro", "Richmond"],
        "intermediate": ["Charlotte", "Chicagoland", "Darlington", "Dover", "Gateway", "Homestead-Miami", "Kansas", "Las Vegas", "Michigan", "Nashville", "New Hampshire", "Phoenix", "Pocono", "Texas"],
        "superspeedway": ["Daytona", "Talladega", "Atlanta"],
        "road": ["Charlotte Roval", "Circuit of the Americas", "Coronado", "Indianapolis Road", "Sonoma", "St. Petersburg", "Watkins Glen"],
    }.items() for name in names
]
