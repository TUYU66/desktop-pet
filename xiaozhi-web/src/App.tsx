import AppRouter from './router';
import { ConfigProvider, App as AntdApp } from 'antd';
import zhCN from 'antd/locale/zh_CN';

const theme = {
  token: {
    colorPrimary: '#5645d4',
    colorInfo: '#5645d4',
    colorSuccess: '#43896e',
    colorWarning: '#ad7c2b',
    colorError: '#c64545',
    borderRadius: 6,
    colorBgContainer: '#ffffff',
    colorBgLayout: '#ffffff',
    fontFamily: `'Microsoft YaHei UI', 'PingFang SC', 'Microsoft YaHei', sans-serif`,
    fontSize: 14,
    colorBorder: '#dededb',
    colorBorderSecondary: '#eeeeec',
    colorFillAlter: '#f7f7f5',
    controlOutline: 'rgba(86, 69, 212, 0.16)',
    colorText: '#37352f',
    colorTextSecondary: '#787671',
  },
  components: {
    Menu: {
      darkItemBg: '#37352f',
      darkSubMenuItemBg: '#37352f',
      darkItemSelectedBg: 'rgba(86, 69, 212, 0.18)',
      darkItemHoverBg: 'rgba(86, 69, 212, 0.08)',
      itemBorderRadius: 8,
      itemSelectedBg: '#eaeae8',
      itemSelectedColor: '#37352f',
      itemHoverBg: '#efefed',
      itemHeight: 44,
    },
    Button: {
      borderRadius: 6,
      controlHeight: 38,
      primaryShadow: 'none',
    },
    Card: {
      borderRadiusLG: 8,
    },
    Input: {
      borderRadius: 6,
      controlHeight: 40,
    },
    Table: {
      borderRadius: 12,
    },
    Modal: {
      borderRadiusLG: 10,
    },
  },
};

function App() {
  return (
    <ConfigProvider locale={zhCN} theme={theme}>
      <AntdApp className="notion-theme">
        <AppRouter />
      </AntdApp>
    </ConfigProvider>
  );
}

export default App;
