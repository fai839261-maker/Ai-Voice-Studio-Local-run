"""Model setup slots; pending requests do not consume active capacity."""
def available_slots(tasks, card_keys, limit):
    capacity=max(1,min(4,int(limit)))
    active=sum(key in card_keys and task.get('phase')!='queued' for key,task in tasks.items())
    return max(0,capacity-active)
