export const avatarColors = ['blue', 'mint', 'peach', 'lilac'] as const;

// Decode, centre-crop and compress before saving into the existing account config.
export async function prepareAvatar(file: File): Promise<string> {
  if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) throw new Error('请选择 JPG、PNG 或 WebP 图片');
  if (file.size > 5 * 1024 * 1024) throw new Error('图片不能超过 5 MB');
  const url = URL.createObjectURL(file);
  try {
    const img = new Image(); img.src = url;
    await img.decode();
    const canvas = document.createElement('canvas'); canvas.width = canvas.height = 192;
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('当前浏览器无法处理图片');
    const side = Math.min(img.naturalWidth, img.naturalHeight);
    ctx.drawImage(img, (img.naturalWidth - side) / 2, (img.naturalHeight - side) / 2, side, side, 0, 0, 192, 192);
    for (const quality of [.85, .65, .45]) {
      const result = canvas.toDataURL('image/webp', quality);
      if (result.length <= 60000) return result;
    }
    throw new Error('图片细节过多，请选择更简单的图片');
  } finally { URL.revokeObjectURL(url); }
}
