import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
BOARD=ROOT/'main/boards/bread-compact-wifi-lcd'


class OtaProtocolTests(unittest.TestCase):
    def test_real_request_loop_matches_identity_checks_crc_and_retries_same_packet(self):
        compiler=os.environ.get('CXX') or shutil.which('g++')
        self.assertTrue(compiler)
        source=(BOARD/'stm32_link_ota.cc').read_text(encoding='utf-8')
        helpers=source[source.index('std::vector<std::string> split('):source.index('\n}\nstd::string Stm32Link::GetOtaStateJson')]
        # Exclude the namespace-closing brace; extracted methods are standalone.
        start=source.index('        auto call=[&](')
        end=source.index('        OtaProgress("bootloader"',start)
        request=source[start:end]
        program=r'''
#include <array>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <deque>
#include <stdexcept>
#include <string>
#include <vector>
#include "link_frame.h"
int64_t tick=0;int writes=0;std::deque<unsigned char> incoming;
std::vector<std::string> sent;int mode=0;
constexpr int kUart=1;
int pdMS_TO_TICKS(int n){return n;}
int64_t esp_timer_get_time(){tick+=1000;return tick;}
HELPERS
int uart_write_bytes(int,const char* bytes,size_t size){
    ++writes;std::string wire(bytes,size);sent.push_back(wire);
    if(mode==1 && writes==1)return (int)size; // lose first ACK
    char decoded[800];memcpy(decoded,bytes+1,size-2);
    assert(link_decode(decoded,size-2)==1);
    auto fields=split(decoded);
    auto prefix=fields[0]+","+fields[1]+","+fields[2]+","+fields[3]+",";
    std::string reply=prefix+(mode==2?"ERR,DATA":"OK,256,2,1");
    char crc[8];snprintf(crc,sizeof(crc),"*%04X\n",link_crc16(reply.data(),reply.size()));
    std::string frame="@"+reply+crc;
    if(mode==3 && writes==1)frame[10]^=1; // bad CRC is never accepted
    for(auto c:frame)incoming.push_back((unsigned char)c);
    return (int)size;
}
int uart_read_bytes(int,uint8_t* p,size_t,int){
    tick+=1000;if(incoming.empty())return 0;*p=incoming.front();incoming.pop_front();return 1;
}
int main(){
    uint32_t session=123,sequence=0;
REQUEST
    auto result=call("HELLO",300,3);assert(result[0]==256 && writes==1);
    sent.clear();writes=0;mode=1;
    result=call("DATA,0,"+std::string(512,'A'),300,3);
    assert(writes==2 && sent[0]==sent[1] && result[0]==256);
    sent.clear();writes=0;mode=3;result=call("HELLO",300,3);assert(writes==2);
    writes=0;mode=2;
    try{call("END",300,3);assert(0);}catch(const std::runtime_error&){}
    assert(writes==1);
    assert(number("4294967295")==UINT32_MAX);
    try{number("4294967296");assert(0);}catch(const std::runtime_error&){}
}
'''.replace('HELPERS',helpers).replace('REQUEST',request)
        with tempfile.TemporaryDirectory() as directory:
            cpp,binary=Path(directory)/'test.cpp',Path(directory)/'test.exe'
            cpp.write_text(program,encoding='utf-8')
            subprocess.run([compiler,'-std=c++14','-finput-charset=UTF-8','-fexec-charset=UTF-8',
                            '-I',str(BOARD),str(cpp),'-o',str(binary)],check=True,capture_output=True)
            subprocess.run([str(binary)],check=True,timeout=10)
