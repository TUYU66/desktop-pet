import request from './request';

export function getDeviceInfo() {
  return request.get('/devices/info');
}

export function getVolume() { return request.get('/devices/volume'); }
export function setVolume(volume: number) { return request.put('/devices/volume', { volume }); }

export function postHeartbeat(data: { deviceId: number; status?: string; battery?: number }) {
  return request.post('/devices/heartbeat', data);
}
