import request from './request';

export function getFirmwareList(page = 1, size = 10) {
  return request.get('/admin/firmware', { params: { page, size } });
}

export function uploadFirmware(description: string, boardType: string, forceUpdate: number, file: File) {
  const formData = new FormData();
  if (description) formData.append('description', description);
  formData.append('boardType', boardType);
  formData.append('forceUpdate', String(forceUpdate));
  formData.append('file', file);
  return request.post('/admin/firmware', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
}

export function deleteFirmware(id: number) {
  return request.delete(`/admin/firmware/${id}`);
}

export function uploadStm32Firmware(file: File) {
  const body = new FormData(); body.append('file', file);
  return request.post('/admin/stm32-ota/upload', body);
}
export function getStm32OtaState(signal?: AbortSignal) { return request.get('/admin/stm32-ota/state', { signal }); }
export function startStm32Ota(id: number, jobId: string, confirmRecovery = false) {
  return request.post(`/admin/stm32-ota/${id}/start`, { jobId, confirmRecovery });
}

export function getEsp32OtaState(signal?: AbortSignal) {
  return request.get('/admin/esp32-ota/state', { signal });
}
export function startEsp32Ota(id: number, jobId: string, connectionId: string) {
  return request.post(`/admin/esp32-ota/${id}/start`, { jobId, connectionId });
}
