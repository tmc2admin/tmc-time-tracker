def version_at_least(version_value, minimum_version):
    try:
        current = tuple(int(part) for part in str(version_value).split('.'))
        minimum = tuple(int(part) for part in str(minimum_version).split('.'))
    except (TypeError, ValueError):
        return False

    width = max(len(current), len(minimum))
    current += (0,) * (width - len(current))
    minimum += (0,) * (width - len(minimum))
    return current >= minimum
