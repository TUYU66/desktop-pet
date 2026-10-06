#include <assert.h>
#include <stdint.h>
#include "../APP/gyro_calibration.h"

static void collect(GyroCalibration *s, uint32_t start, int noisy)
{
    unsigned int n;
    for (n=1;n<=600;++n)
        GyroCal_Observe(s,start+n*5U,-20.7f,29.0f+(noisy?(n%2?10:-10):(n%2?1:-1)),-6.0f,1);
}

int main(void)
{
    GyroCalibration s={0};
    unsigned int revision;
    GyroCal_Begin(&s,100,-20.7f,29,-6);
    GyroCal_Observe(&s,100,-20.7f,29,-6,1);
    assert(s.samples==0);
    collect(&s,100,0);
    assert(s.state==2 && s.valid && s.samples==600);
    assert(s.pitch_bias>28.99f && s.pitch_bias<29.01f && s.yaw_bias==-6);
    revision=s.revision;

    GyroCal_Begin(&s,4000,-20.7f,29,-6);
    GyroCal_Observe(&s,4005,-20.7f,29,-6,0);
    assert(s.state==3 && s.error==1 && s.valid && s.revision==revision);
    assert(s.pitch_bias>28.99f);
    GyroCal_Begin(&s,5000,-20.7f,29,-6);
    GyroCal_Observe(&s,5005,-20.2f,29,-6,1);
    assert(s.state==3 && s.error==2);
    GyroCal_Begin(&s,6000,-20.7f,29,-6);
    GyroCal_Observe(&s,6051,-20.7f,29,-6,1);
    assert(s.state==3 && s.error==3);
    GyroCal_Begin(&s,6100,-20.7f,29,-6);
    {
        unsigned int n;
        for(n=1;n<=251;++n) GyroCal_Observe(&s,6100+n*20U,-20.7f,29,-6,1);
    }
    assert(s.state==3 && s.error==4 && s.revision==revision);
    GyroCal_Begin(&s,7000,-20.7f,29,-6);
    collect(&s,7000,1);
    assert(s.state==3 && s.error==2 && s.revision==revision);
    GyroCal_Begin(&s,11000,-20.7f,100,-6);
    {
        unsigned int n;
        for(n=1;n<=600;++n) GyroCal_Observe(&s,11000+n*5U,-20.7f,100,-6,1);
    }
    assert(s.state==3 && s.error==5 && s.revision==revision);
    GyroCal_Begin(&s,15000,-20.7f,29,-6);
    GyroCal_Fail(&s,6);
    assert(s.state==3 && s.error==6 && s.valid);
    GyroCal_Clear(&s);
    assert(!s.valid && s.state==0 && s.pitch_bias==0 && s.yaw_bias==0);
    GyroCal_Begin(&s,UINT32_MAX-1000U,-20.7f,29,-6);
    collect(&s,UINT32_MAX-1000U,0);
    assert(s.state==2 && s.valid);
    GyroCal_Begin(&s,20000,-20.7f,29,-6);
    {
        unsigned int n;
        for(n=1;n<=600;++n)
            GyroCal_Observe(&s,20000+n*5U,-20.7f,29+(n==20?20:(n%2?5:-5)),-6+(n%2?4:-4),1);
    }
    assert(s.state==2 && s.valid); /* Bounded noise and one spike do not abort. */
    assert(s.pitch_bias>28.9f && s.pitch_bias<29.1f);
    GyroCal_Begin(&s,25000,-20.7f,29,-6);
    GyroCal_Observe(&s,25025,-20.7f,29,-6,1);
    assert(s.state==1 && s.samples==1); /* One short scheduling delay is tolerated. */
    GyroCal_Begin(&s,30000,-20.7f,29,-6);
    {
        unsigned int n;
        for(n=1;n<=600;++n) {
            if(n>=10 && n<=13) continue; /* Failed reads are not counted. */
            GyroCal_Observe(&s,30000+n*5U,-20.7f,29,-6,1);
        }
    }
    assert(s.state==2 && s.samples==596 && s.pitch_bias==29 && s.yaw_bias==-6);
    return 0;
}
