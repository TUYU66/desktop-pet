"""Creation success belongs to immutable request metadata, not delivery status."""
STATUS = {'scheduled':'等待发送','dispatching':'正在发送','awaiting_confirmation':'等待回应',
          'retry_pending':'等待下次提醒','delivery_unknown':'发送待核实','missed':'本轮未回应',
          'completed':'已完成','cancelled':'已取消','expired':'已过期','active':'进行中',
          'paused':'已暂停','history_removed':'历史已清理'}


def verified(result, body, device):
    if not isinstance(result,dict) or result.get('id')!=body['requestId']: return False
    value=result.get('creation')
    return (isinstance(value,dict) and value.get('requestId')==body['requestId']
            and value.get('deviceId')==device and value.get('title')==body['title']
            and value.get('triggerAt')==body.get('triggerAt')
            and value.get('recurrence')==body.get('recurrence','once')
            and value.get('seconds')==body.get('seconds')
            and isinstance(result.get('current'),dict) and result['current'].get('status') in STATUS)


def status_text(result):
    return STATUS.get(result.get('current',{}).get('status'),'状态待核实')
