def binary_search(values: list[int], target: int) -> int:
    low = 0
    high = len(values) - 1

    while low < high:
        mid = (low + high) // 2
        candidate = values[mid]
        if candidate == target:
            return mid
        if candidate < target:
            low = mid + 1
        else:
            high = mid - 1

    return -1
