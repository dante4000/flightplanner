"""Row-tolerant replacement for fast_flights.parser.parse_js (fast-flights==3.0.2 only)."""
from __future__ import annotations

import json

import fast_flights.parser as P
from fast_flights.exceptions import FlightsNotFound
from fast_flights.model import (
    Airline,
    Airport,
    Alliance,
    CarbonEmission,
    Flights,
    JsMetadata,
    SimpleDatetime,
    SingleFlight,
)


def tolerant_parse_js(js: str):
    data = js.split("data:", 1)[1].rsplit(",", 1)[0]
    if data.endswith("errorHasStatus: true"):
        raise FlightsNotFound("no flights found; received error")
    payload = json.loads(data)

    flights = P.ResultList()
    try:
        flights.metadata = JsMetadata(
            alliances=[Alliance(code=c, name=n) for c, n in payload[7][1][0]],
            airlines=[Airline(code=c, name=n) for c, n in payload[7][1][1]],
        )
    except (IndexError, TypeError):
        flights.metadata = JsMetadata(alliances=[], airlines=[])

    rows = []
    for idx in (2, 3):
        try:
            grp = payload[idx][0]
        except (IndexError, TypeError):
            continue
        if isinstance(grp, list):
            rows.extend(grp)

    for k in rows:
        try:
            flight = k[0]
            price = k[1][0][1]
            sg = []
            for s in flight[2]:
                sg.append(
                    SingleFlight(
                        from_airport=Airport(code=s[3], name=s[4]),
                        to_airport=Airport(code=s[6], name=s[5]),
                        departure=SimpleDatetime(date=s[20], time=s[8]),
                        arrival=SimpleDatetime(date=s[21], time=s[10]),
                        duration=s[11],
                        plane_type=s[17],
                    )
                )
            extras = flight[22]
            flights.append(
                Flights(
                    type=flight[0],
                    price=price,
                    airlines=flight[1],
                    flights=sg,
                    carbon=CarbonEmission(emission=extras[7], typical_on_route=extras[8]),
                )
            )
        except (IndexError, TypeError):
            continue
    return flights


def install() -> None:
    P.parse_js = tolerant_parse_js
