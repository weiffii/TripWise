# TripWise — AI/DS brief (draft, owner approval required)

## User and problem
A tourist wants a feasible, personalized itinerary instead of manually combining attraction lists, opening hours, travel times and breaks. This is the working hypothesis, NOT a verified interview finding.

## Confirmed product decisions
- 8 categories: museums, history, nature, cafes, entertainment, shopping, viewpoints, unusual.
- 1–2 priority interests, optional secondary interests.
- Popular and highly rated attractions prioritized with personalization and freshness, where evidenced by data.
- Pace: relaxed / balanced / intensive.
- Travel modes: walking / public transit / car.
- Cafes: separate interest or scheduled food stop.

## Proposed first AI/DS MVP (requires team approval)
1. Accept a tourist preference profile plus a fixture list of POIs.
2. Filter invalid/unavailable POIs and rank the rest transparently.
3. Return top POIs with reason codes and a simple feasible itinerary subject to time budget; first routing implementation may use a clearly marked test travel-time matrix.

## Questions for the team
- Which backend language and exact API contracts exist today?
- What city and lawful POI data source will the MVP use?
- Which mandatory locations, start/end point, budget and dates are currently supported?
- Who owns frontend design and who is responsible for map/routing APIs?
- How will 3–5 interview and usability tests be conducted and documented?

## Non-goals for the first iteration
Training a neural network, automatically creating many independent production AI agents, paid APIs, fabricated real-time transit data.
