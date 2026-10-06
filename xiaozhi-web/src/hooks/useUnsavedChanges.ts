import { useEffect } from 'react';

export default function useUnsavedChanges(dirty: boolean) {
  useEffect(() => {
    if (!dirty) return;
    const unload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    const confirm = (event: Event) => { if (!window.confirm('有尚未保存的修改，确定离开吗？')) event.preventDefault(); };
    const link = (event: MouseEvent) => {
      const anchor = (event.target as Element).closest?.('a');
      if (!anchor || anchor.target === '_blank' || event.ctrlKey || event.metaKey || anchor.hash === '#main-content') return;
      if (anchor.href !== window.location.href && !window.confirm('有尚未保存的修改，确定离开吗？')) { event.preventDefault(); event.stopPropagation(); }
    };
    window.addEventListener('beforeunload', unload);
    window.addEventListener('xiaozhi:before-leave', confirm);
    document.addEventListener('click', link, true);
    return () => { window.removeEventListener('beforeunload', unload); window.removeEventListener('xiaozhi:before-leave', confirm); document.removeEventListener('click', link, true); };
  }, [dirty]);
}
