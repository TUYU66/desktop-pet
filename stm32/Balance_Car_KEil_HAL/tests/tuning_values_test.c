/* Host-only parser/envelope regression. No motor or sensor access. */
#include <assert.h>
#include "../APP/tuning_values.h"
#include "../APP/calibration_deadline.h"

int main(void)
{
    long values[9], p[6] = {2100,960000,5000,620000,1500,600};
    unsigned int i;
    const char *bad[] = {"", "1,2", "1,2,3,4", "1,2,3x", "1,2,3,", "1,+2,3",
                         "1, 2,3", "1,2147483648,3", "1,-2147483648,3"};
    assert(Tuning_ValuesValid(p));
    for (i=0;i<sizeof(bad)/sizeof(bad[0]);++i) assert(!Tuning_Parse(bad[i],values,3));
    assert(Tuning_Parse("65535,1,2147483647,-10000,2000000,20000,1200000,10000,6000",values,9));
    assert(Tuning_ValuesValid(values+3));
    p[0]=-10001; assert(!Tuning_ValuesValid(p)); p[0]=2100;
    p[1]=99999; assert(!Tuning_ValuesValid(p)); p[1]=960000;
    p[4]=10001; assert(!Tuning_ValuesValid(p)); p[4]=1500;
    p[5]=-1; assert(!Tuning_ValuesValid(p));
    assert(Calibration_SampleFresh(0x80000010U,0x7FFFFFF0U));
    assert(!Calibration_SampleFresh(1500,0));
    assert(!Calibration_SampleFresh(100,200));
    return 0;
}
