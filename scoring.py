from typing import Any, Dict, List


def leg_prob(side: str, yes_price: float) -> float:
    if side == "yes":
        return yes_price
    else:
        return (1 - yes_price)

def frechet_ceiling(leg_probs: List[float]) -> float:
    smallest = leg_probs[0]
    for i in leg_probs:
        if i < smallest:
            smallest = i
    return smallest

def frechet_floor(leg_probs: List[float]) -> float:
    total = 0.0
    for i in leg_probs:
        total += i
    total -= len(leg_probs) - 1
    if total <= 0:
        return 0.0
    else:
        return total

def independence_price(leg_probs: List[float]) -> float:
    product = leg_probs[0]
    for i in leg_probs[1:]:
        product *= i
    return product

def score_fill(fill_price: float, leg_probs: List[float]) -> Dict[str, Any]:
    return {
        "ceiling": frechet_ceiling(leg_probs),
        "floor": frechet_floor(leg_probs),
        "independence": independence_price(leg_probs),
        "gap_to_ceiling": (fill_price - frechet_ceiling(leg_probs)),
        "gap_to_independence": (fill_price - independence_price(leg_probs)),
        "coherent": (fill_price <= frechet_ceiling(leg_probs) + 1e-9),
    }
