"""Product hierarchy supplied by the curated metadata examples."""
import re
import json
PRODUCT_PARENTS = {'i-Ready Classroom Mathematics': 'Core Math'}


def product_mentions(texts, choices):
    """Retain contextual mentions for model review, never automatic product tags.

    Match longer names first so i-Ready Classroom Mathematics does not also
    nominate i-Ready. Negations and unrelated mentions remain visible in context.
    """
    mentions = []
    ordered = sorted(choices, key=len, reverse=True)
    patterns = [(name, re.compile(
        r'(?<!\w)' + r'[\s\-\u2010-\u2015]*'.join(
            re.escape(word) for word in re.split(r'[\s-]+', name)) + r'(?!\w)',
        re.IGNORECASE)) for name in ordered]
    for source, text in texts:
        searchable = text.replace('_', ' ')
        occupied = []
        for name, pattern in patterns:
            for match in pattern.finditer(searchable):
                if any(match.start() < end and match.end() > start for start, end in occupied):
                    continue
                occupied.append(match.span())
                mentions.append({'source': source, 'candidate': name,
                                 'offset': match.start(),
                                 'text': text[max(0, match.start() - 350):match.end() + 650]})
    return mentions


def transcript_product_mentions(texts, choices):
    """Compatibility helper for callers requesting speech evidence only."""
    return product_mentions([(source, text) for source, text in texts if
        re.search(r'transcript|transcription|speech|caption|subtitle', source, re.IGNORECASE)
        or '"transcript_snippets"' in text], choices)


def product_mention_batches(mentions, budget=12000):
    """Partition all contextual mentions without silently truncating evidence."""
    batches, batch = [], []
    for mention in mentions:
        if len(json.dumps([mention], ensure_ascii=False)) > budget:
            raise ValueError('A product evidence excerpt exceeds the request budget.')
        if batch and len(json.dumps(batch + [mention], ensure_ascii=False)) > budget:
            batches.append(batch)
            batch = []
        batch.append(mention)
    if batch:
        batches.append(batch)
    return batches


def normalize_products(value, choices):
    if not isinstance(value, list):
        raise ValueError('Product must be an array')
    normalized = []
    for product in value:
        if isinstance(product, str):
            product = {'Primary': PRODUCT_PARENTS.get(product), 'Secondary': product}
        if not isinstance(product, dict) or set(product) != {'Primary', 'Secondary'}:
            raise ValueError('Product entries require Primary and Secondary')
        secondary, primary = product['Secondary'], product['Primary']
        if not isinstance(secondary, str) or secondary not in choices:
            raise ValueError('Product Secondary must be an allowed product')
        expected = PRODUCT_PARENTS.get(secondary)
        if primary != expected:
            raise ValueError('Product Primary does not match the known product hierarchy')
        if product not in normalized:
            normalized.append(product)
    return normalized
