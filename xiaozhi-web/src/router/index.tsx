import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { lazy, Suspense } from 'react';
import { Spin } from 'antd';
import { useUserStore } from '../store/userStore';
import MainLayout from '../layouts/MainLayout';
import LoginLayout from '../layouts/LoginLayout';
const Login = lazy(() => import('../pages/login/Login'));
const Dashboard = lazy(() => import('../pages/dashboard/Dashboard'));
const OtaList = lazy(() => import('../pages/ota/OtaList'));
const ChatRoom = lazy(() => import('../pages/chat/ChatRoom'));
const DeviceList = lazy(() => import('../pages/device/DeviceList'));
const Settings = lazy(() => import('../pages/settings/Settings'));
const Account = lazy(() => import('../pages/account/Account'));
const MemoryList = lazy(() => import('../pages/memory/MemoryList'));
const Music = lazy(() => import('../pages/music/Music'));
const Reminders = lazy(() => import('../pages/reminders/Reminders'));

function LazyLoad({ children }: { children: React.ReactNode }) {
  return <Suspense fallback={<Spin style={{ display: 'block', margin: '200px auto' }} />}>{children}</Suspense>;
}

function AuthGuard({ children }: { children: React.ReactNode }) {
  const token = useUserStore((s) => s.token);
  if (!token) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export default function AppRouter() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<LoginLayout />}>
          <Route path="/login" element={<LazyLoad><Login /></LazyLoad>} />
        </Route>
        <Route element={<AuthGuard><MainLayout /></AuthGuard>}>
          <Route path="/" element={<LazyLoad><Dashboard /></LazyLoad>} />
          <Route path="/chat" element={<LazyLoad><ChatRoom /></LazyLoad>} />
          <Route path="/memory" element={<LazyLoad><MemoryList /></LazyLoad>} />
          <Route path="/music" element={<LazyLoad><Music /></LazyLoad>} />
          <Route path="/reminders" element={<LazyLoad><Reminders /></LazyLoad>} />
          <Route path="/settings" element={<LazyLoad><Settings /></LazyLoad>} />
          <Route path="/device" element={<LazyLoad><DeviceList /></LazyLoad>} />
          <Route path="/account" element={<LazyLoad><Account /></LazyLoad>} />
          <Route path="/ota" element={<LazyLoad><OtaList /></LazyLoad>} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
