import AppRouter from './router';
import { ConfigProvider, App as AntdApp } from 'antd';
import zhCN from 'antd/locale/zh_CN';

const theme = {
  token: {
    colorPrimary: '#3867a6',
    colorInfo: '#3867a6',
    colorSuccess: '#10b981',
    colorWarning: '#f59e0b',
    colorError: '#ef4444',
    borderRadius: 10,
    colorBgContainer: '#ffffff',
    colorBgLayout: '#f3f6fb',
    fontFamily: `'Microsoft YaHei UI', 'PingFang SC', 'Microsoft YaHei', sans-serif`,
    fontSize: 14,
    colorText: '#25354b',
    colorTextSecondary: '#6b7280',
  },
  components: {
    Menu: {
      darkItemBg: '#18283e',
      darkSubMenuItemBg: '#18283e',
      darkItemSelectedBg: 'rgba(56, 103, 166, 0.18)',
      darkItemHoverBg: 'rgba(56, 103, 166, 0.08)',
      itemBorderRadius: 8,
    },
    Button: {
      borderRadius: 10,
      controlHeight: 38,
      primaryShadow: 'none',
    },
    Card: {
      borderRadiusLG: 14,
    },
    Input: {
      borderRadius: 10,
      controlHeight: 40,
    },
    Table: {
      borderRadius: 12,
    },
    Modal: {
      borderRadiusLG: 16,
    },
  },
};

function App() {
  return (
    <ConfigProvider locale={zhCN} theme={theme}>
      <AntdApp>
        <AppRouter />
      </AntdApp>
    </ConfigProvider>
  );
}

export default App;
