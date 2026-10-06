import request from './request';

export interface LoginParams {
  username: string;
  password: string;
}

export interface LoginResult {
  token: string;
  userId: number;
  username: string;
  superAdmin: number;
}

export function login(params: LoginParams) {
  return request.post<any, LoginResult>('/auth/login', params);
}

export function register(params: { username: string; password: string }) {
  return request.post('/auth/register', params);
}

export function logout() {
  return request.post('/auth/logout');
}
