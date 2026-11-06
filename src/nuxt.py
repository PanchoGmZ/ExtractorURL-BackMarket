"""Decode the data-only subset of Nuxt/devalue. Never evaluate page scripts."""
import json
import re

UUID_RE = re.compile(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', re.I)


def decode(payload):
    if not isinstance(payload, list) or not payload or len(payload) > 100_000:
        raise ValueError('Invalid or excessive Nuxt data table')
    cache = {}

    def revive(index, depth=0):
        if type(index) is not int or depth > 80:
            raise ValueError('Invalid Nuxt reference/depth')
        if index < 0:
            return 0 if index == -5 else None
        if index >= len(payload):
            raise ValueError('Nuxt reference out of range')
        if index in cache:
            return cache[index]
        value = payload[index]
        if isinstance(value, dict):
            output = {}
            cache[index] = output
            output.update({key: revive(ref, depth + 1) for key, ref in value.items()})
        elif isinstance(value, list):
            if value and isinstance(value[0], str):
                tag = value[0]
                if tag in {'Reactive', 'ShallowReactive', 'Ref', 'ShallowRef', 'EmptyRef', 'EmptyShallowRef'}:
                    cache[index] = None
                    output = revive(value[1], depth + 1) if len(value) > 1 else None
                elif tag in {'Date', 'BigInt'}:
                    output = value[1] if len(value) > 1 else None
                else:
                    # Unknown/custom revivers are deliberately not executed.
                    output = None
            else:
                output = []
                cache[index] = output
                output.extend(revive(ref, depth + 1) for ref in value)
        else:
            output = value
        cache[index] = output
        return output

    return revive(0)


def find_commerce(root, product_id):
    """Only select unambiguous objects associated with the URL's UUID."""
    candidates = {key: {} for key in ('product', 'pickers', 'specifications')}
    visited = set()
    count = 0

    def walk(value, bound=False, depth=0):
        nonlocal count
        if depth > 80 or not isinstance(value, (dict, list)):
            return
        marker = (id(value), bound)
        if marker in visited:
            return
        visited.add(marker)
        count += 1
        if count > 50_000:
            raise ValueError('Excessive embedded data graph')
        if isinstance(value, dict):
            own_id = value.get('productId')
            if isinstance(own_id, str) and UUID_RE.fullmatch(own_id):
                bound = own_id.lower() == product_id
            groups = value.get('pickerGroups')
            if isinstance(groups, list):
                selected_ids = {
                    item.get('productId', '').lower()
                    for group in groups if isinstance(group, dict)
                    for item in group.get('items', []) if isinstance(item, dict)
                    if item.get('selected') is True and isinstance(item.get('productId'), str)
                }
                bound = bound or product_id in selected_ids
            kind = None
            if isinstance(value.get('titles'), dict) and isinstance(value.get('images'), list):
                kind = 'product'
            elif isinstance(groups, list):
                kind = 'pickers'
            elif isinstance(value.get('specifications'), list):
                kind = 'specifications'
            if bound and kind:
                candidates[kind][id(value)] = value
            for key, child in value.items():
                ids = UUID_RE.findall(key)
                child_bound = product_id in [item.lower() for item in ids] if ids else bound
                walk(child, child_bound, depth + 1)
        else:
            for child in value:
                walk(child, bound, depth + 1)

    walk(root)
    output = {}
    for kind, values in candidates.items():
        # Duplicate copies of identical data are harmless; differing caches are ambiguous.
        unique = {json.dumps(value, sort_keys=True, ensure_ascii=False): value for value in values.values()}
        if len(unique) == 1:
            output[kind] = next(iter(unique.values()))
    return output
