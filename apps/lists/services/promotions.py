from django.db.models import Avg

from apps.lists.models import ListItem


def get_promotions(user):
    promotions = []

    current_items = (
        ListItem.objects
        .filter(
            list__owner=user,
            list__is_completed=False,
            price__isnull=False,
        )
        .select_related('product')
    )

    for item in current_items:
        historical_average = (
            ListItem.objects
            .filter(
                list__owner=user,
                list__is_completed=True,
                product=item.product,
                price__isnull=False,
            )
            .aggregate(
                average=Avg('price'),
            )
            ['average']
        )

        if historical_average is None:
            continue

        if item.price >= historical_average:
            continue

        promotions.append(
            {
                'product': item.product.name,
                'current_price': item.price,
                'historical_average': historical_average,
                'savings': (
                    historical_average
                    - item.price
                ),
            }
        )

    return promotions