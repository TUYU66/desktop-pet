import request from './request';

export function getFirmwareList(page = 1, size = 10) {
  return request.get('/admin/firmware', { params: { page, size } });
}

export function uploadFirmware(version: string, description: string, boardType: string, forceUpdate: number, file: File) {
  const formData = new FormData();
  formData.append('version', version);
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
