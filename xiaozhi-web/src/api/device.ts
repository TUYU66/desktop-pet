import request from './request';

export function getDeviceInfo() {
  return request.get('/devices/info');
}

export function setVolume(volume: number) { return request.put('/devices/volume', { volume }); }
