"""The single eleven-field record constructor shared by all extraction methods."""


def product_record(data, *, title=None, price=None, weight=None, images=None,
                   characteristics=None, variations=None):
    return {'url': data['url'], 'title': title, 'price': price,
            'productLadderPrices': [], 'moq': None, 'weight': weight, 'dimensions': None,
            'images': images or None, 'characteristics': characteristics or None,
            'variations': variations, 'productId': data['productId']}
