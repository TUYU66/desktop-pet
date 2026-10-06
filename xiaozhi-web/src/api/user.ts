import request from './request';

export function getUserInfo() {
  return request.get('/user/info');
}

export function updatePassword(currentPassword: string, newPassword: string) {
  return request.put('/user/password', { currentPassword, newPassword });
}

export function getBotConfig() {
  return request.get('/user/config');
}

export function getWakeWordStatus() {
  return request.get('/user/wake-word-status');
}

export function saveBotConfig(data: Record<string, string>) {
  return request.put('/user/config', data);
}

export function getPubConfig() {
  return request.get('/user/pub-config');
}
