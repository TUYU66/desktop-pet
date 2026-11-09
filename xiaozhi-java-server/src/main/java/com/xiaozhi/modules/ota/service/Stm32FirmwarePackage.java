package com.xiaozhi.modules.ota.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.Map;
import java.util.Set;
import java.util.zip.CRC32;
import java.util.zip.ZipFile;

/** Bounded ZIP inspection: never extracts client-provided paths. */
public final class Stm32FirmwarePackage {
    public static final String BOARD_TYPE="stm32-YB-EST01-V2.0";
    private static final ObjectMapper JSON=new ObjectMapper();
    public record Validated(long version, byte[] image, long crc, String sha, boolean compiledVersion) {
        public Map<String,Object> metadata() {
            return Map.of("board","YB-EST01-V2.0","mcu","STM32F103RCT6","load_address",0x08008000,
                "image_size",image.length,"firmware_version",version,"crc32_value",crc,"sha256",sha,"ota_application_abi",1);
        }
    }
    private static void require(boolean condition,String error) {
        if(!condition) throw new IllegalArgumentException(error);
    }
    private static long integer(JsonNode root,String key) {
        JsonNode value=root.path(key);
        require(value.isIntegralNumber()&&value.canConvertToLong(),"固件元数据数值无效："+key);
        return value.longValue();
    }
    public static Validated read(Path path) throws IOException {
        require(java.nio.file.Files.size(path)<=1024*1024,"固件包不能超过 1MB");
        try(var zip=new ZipFile(path.toFile())) {
            var entries=zip.stream().toList();
            require(entries.size()==2 && entries.stream().map(e->e.getName()).collect(java.util.stream.Collectors.toSet())
                .equals(Set.of("manifest.json","firmware.bin")),"请选择打包工具生成的 STM32 ZIP 固件包");
            byte[] manifest,image;
            try(var in=zip.getInputStream(zip.getEntry("manifest.json"))) {manifest=in.readNBytes(8193);}
            try(var in=zip.getInputStream(zip.getEntry("firmware.bin"))) {image=in.readNBytes(110593);}
            require(manifest.length<=8192 && image.length>=8 && image.length<=110592 && image.length%2==0,"固件包长度无效");
            JsonNode m=JSON.readTree(manifest);
            require(m!=null&&m.isObject(),"固件清单格式错误");
            require(m.path("format").asText().equals("desktop-pet-stm32-ota") && integer(m,"format_version")==1 &&
                integer(m,"layout_version")==1 && m.path("board").asText().equals("YB-EST01-V2.0") &&
                m.path("mcu").asText().equals("STM32F103RCT6") && integer(m,"load_address")==0x08008000,"固件型号或分区不匹配");
            long version=integer(m,"firmware_version"),original=integer(m,"original_size");
            require(version>0&&version<=0xFFFFFFFFL,"固件版本必须为有效递增整数");
            require(integer(m,"image_size")==image.length && original>=8 && (original==image.length ||
                original==image.length-1 && original%2==1 && image[image.length-1]==(byte)255),"固件长度与清单不匹配");
            var vectors=ByteBuffer.wrap(image).order(ByteOrder.LITTLE_ENDIAN);
            long stack=Integer.toUnsignedLong(vectors.getInt()),entry=Integer.toUnsignedLong(vectors.getInt());
            require(stack>0x20000000L&&stack<=0x2000C000L&&stack%8==0&&(entry&1)==1&&
                (entry&~1L)>=0x08008008L&&(entry&~1L)<0x08008000L+image.length,"固件启动地址错误，请使用偏移应用 BIN 打包");
            require(integer(m,"ota_application_abi")==1,"固件缺少 OTA 发布声明，请使用新版打包工具");
            byte[] magic="PETOTAAPPV1\0".getBytes(java.nio.charset.StandardCharsets.US_ASCII);
            int descriptor=-1;
            for(int i=0;i+magic.length<=image.length;i++) {
                if(java.util.Arrays.equals(image,i,i+magic.length,magic,0,magic.length)) {
                    require(descriptor<0,"固件含有重复的 OTA 发布声明");descriptor=i;
                }
            }
            require(descriptor>=0&&descriptor+32<=image.length,"固件没有 OTA 入口发布声明，拒绝安装");
            var release=ByteBuffer.wrap(image).order(ByteOrder.LITTLE_ENDIAN);release.position(descriptor+12);
            int format=release.getInt();
            require((format==1||format==2)&&release.getInt()==0x08008000&&release.getInt()==110592&&(release.getInt()&8192)!=0,"OTA 发布声明不匹配");
            long otaEntry=Integer.toUnsignedLong(release.getInt());
            require((otaEntry&1)==1&&(otaEntry&~1L)>=0x08008008L&&(otaEntry&~1L)<0x08008000L+image.length,"固件未链接有效 OTA 入口");
            if(format==2) require(descriptor+36<=image.length && Integer.toUnsignedLong(release.getInt())==version,
                    "清单版本与固件内部版本不一致");
            CRC32 crc=new CRC32();crc.update(image);
            String sha;
            try {sha=HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(image));}
            catch(java.security.NoSuchAlgorithmException e) {throw new IllegalStateException(e);}
            require(String.format("%08x",crc.getValue()).equals(m.path("crc32").asText())&&sha.equals(m.path("sha256").asText()),"固件校验失败");
            return new Validated(version,image,crc.getValue(),sha,format==2);
        }
    }
    /** 升级前拒绝已安装/旧版本，恢复模式仍交由 Bootloader 校验可恢复的镜像。 */
    public static void requireNewerVersion(long candidate, java.util.Map<?,?> state) {
        if(Boolean.TRUE.equals(state.get("recovery"))) return;
        long installed=reportedVersion(state.get("installedVersion"));
        if("completed".equals(state.get("phase")) && !Boolean.TRUE.equals(state.get("active")))
            installed=Math.max(installed,reportedVersion(state.get("version")));
        if(installed>0 && candidate<=installed)
            throw new IllegalArgumentException(candidate==installed?"底盘已是当前版本，无需重复升级":"固件版本低于底盘已安装版本，请选择新版本");
    }
    private static long reportedVersion(Object value) {
        if(value instanceof Number number) {
            double n=number.doubleValue();
            return Double.isFinite(n)&&n>0&&n<=0xFFFFFFFFL&&n==Math.floor(n)?number.longValue():0;
        }
        if(value instanceof String text && text.matches("[1-9][0-9]{0,9}")) {
            long n=Long.parseLong(text);
            return n<=0xFFFFFFFFL?n:0;
        }
        return 0;
    }
    private Stm32FirmwarePackage() {}
}
