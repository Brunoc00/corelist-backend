from decimal import Decimal
from .gemini import generate_with_gemini
from django.db.models import F, Sum


ZERO = Decimal('0.00')
ONE_HUNDRED = Decimal('100')


def _get_historical_average(user):
    completed_lists = (
        user.lists
        .filter(is_completed=True)
        .annotate(
            calculated_total=Sum(
                F('items__quantity') * F('items__price')
            )
        )
    )

    totals = [
        shopping_list.calculated_total or ZERO
        for shopping_list in completed_lists
    ]

    if not totals:
        return ZERO

    return (
        sum(totals, ZERO)
        / len(totals)
    )


def _get_current_purchase_data(user):
    active_lists = (
        user.lists
        .filter(is_completed=False)
        .annotate(
            calculated_total=Sum(
                F('items__quantity') * F('items__price')
            )
        )
    )

    current_total = sum(
        (
            shopping_list.calculated_total or ZERO
            for shopping_list in active_lists
        ),
        ZERO,
    )

    active_list = active_lists.first()

    if active_list:
        budget = active_list.budget or ZERO
    else:
        budget = ZERO

    return {
        'current_total': current_total,
        'budget': budget,
    }


def _calculate_percentage_change(
    historical_difference,
    historical_average,
):
    if historical_average == 0:
        return ZERO

    return (
        historical_difference
        / historical_average
        * ONE_HUNDRED
    )


def build_insights_context(user):
    historical_average = _get_historical_average(
        user=user,
    )

    current_purchase = _get_current_purchase_data(
        user=user,
    )

    current_total = current_purchase['current_total']
    budget = current_purchase['budget']

    budget_remaining = (
        budget - current_total
    )

    historical_difference = (
        current_total - historical_average
    )

    historical_percentage_change = (
        _calculate_percentage_change(
            historical_difference=historical_difference,
            historical_average=historical_average,
        )
    )

    return {
        'historical_average': historical_average,
        'current_total': current_total,
        'budget': budget,
        'budget_remaining': budget_remaining,
        'historical_difference': historical_difference,
        'historical_percentage_change': (
            historical_percentage_change
        ),
    }


def generate_insights(user):
    context = build_insights_context(
        user=user,
    )

    return generate_with_gemini(
        context=context,
    )