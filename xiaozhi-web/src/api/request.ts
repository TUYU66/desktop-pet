import axios from 'axios';
import type { AxiosResponse } from 'axios';

type ErrorDetails = {
  status?: number;
  code?: number | string;
  transportCode?: string;
  response?: AxiosResponse;
  originalError?: unknown;
};

// status is the HTTP status; code is the business code (including HTTP 200 errors).
export class ApiError extends Error {
  readonly status?: number;
  readonly code?: number | string;
  readonly transportCode?: string;
  readonly response?: AxiosResponse;
  readonly originalError?: unknown;

  constructor(message: string, details: ErrorDetails = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = details.status;
    this.code = details.code;
    this.transportCode = details.transportCode;
    this.response = details.response;
    this.originalError = details.originalError;
  }
}

function safeMessage(value: unknown): string | undefined {
  if (typeof value !== 'string') return;
  const text = value.trim();
  // Server pages, stack traces and oversized diagnostics are not user messages.
  if (!text || text.length > 300 || /[<>\x00-\x08\x0b-\x1f]/.test(text) ||
    /traceback|\bexception\b|\bstack\s*trace\b|[\w.$]*(?:Exception|Error)(?:[:\s]|$)|\bat\s+\S+\s*\([^)]*:\d+|\bfile\s+".*",\s*line\s+\d+/i.test(text)) return;
  return text.replace(/\s+/g, ' ');
}

function errorBody(data: unknown): { code?: number | string; message?: string } {
  if (!data || typeof data !== 'object' || Array.isArray(data)) return {};
  const body = data as Record<string, unknown>;
  return {
    code: typeof body.code === 'number' || typeof body.code === 'string' ? body.code : undefined,
    message: safeMessage(body.msg) || safeMessage(body.message),
  };
}

function requireLogin() {
  localStorage.removeItem('token');
  localStorage.removeItem('user');
  window.location.href = '/login';
}

const request = axios.create({ baseURL: '/xiaozhi/api', timeout: 30000 });

request.interceptors.request.use((config) => {
  const token = localStorage.getItem('token');
  if (token) config.headers.Authorization = `Bearer ${token}`;
  // 不要覆盖文件上传的 Content-Type
  if (config.data instanceof FormData) {
    delete config.headers['Content-Type'];
  } else if (!config.headers['Content-Type']) {
    config.headers['Content-Type'] = 'application/json;charset=utf-8';
  }
  return config;
});

request.interceptors.response.use(
  (response) => {
    const body = errorBody(response.data);
    if (body.code !== undefined && body.code !== 0 && body.code !== '0') {
      if (body.code === 401 || body.code === '401') requireLogin();
      return Promise.reject(new ApiError(body.message || '请求未完成，请稍后核实', {
        code: body.code, status: response.status, response, originalError: response.data,
      }));
    }
    return response.data;
  },
  (error: unknown) => {
    if (error instanceof ApiError) return Promise.reject(error);
    if (!axios.isAxiosError(error)) {
      return Promise.reject(new ApiError('请求未完成，请稍后核实', { originalError: error }));
    }
    const status = error.response?.status;
    const body = errorBody(error.response?.data);
    if (status === 401 || body.code === 401 || body.code === '401') requireLogin();
    const timedOut = error.code === 'ECONNABORTED' || error.code === 'ETIMEDOUT' || status === 408 || status === 504;
    const message = timedOut ? '请求超时，结果未确认，请核实后再操作' : body.message ||
      (error.code === 'ERR_CANCELED' ? '请求已取消，结果未确认，请核实' :
        !error.response ? '无法连接服务，结果未确认，请检查连接后核实' :
          status === 401 ? '登录已失效，请重新登录' :
            status === 403 ? '当前账号无权执行此操作' :
              status === 404 ? '请求的内容或接口不存在' :
                status === 410 ? '请求记录已过期，请核实当前状态' : '服务暂时无法处理请求，请稍后核实');
    return Promise.reject(new ApiError(message, {
      status, code: body.code, transportCode: error.code, response: error.response, originalError: error,
    }));
  },
);

export default request;
