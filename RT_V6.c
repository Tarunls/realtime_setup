/*
 * ScintPi3.0 Firmware
 * Updates 8th Jun 2021:
 * - New Checksum version 2020.
 * - Removed bitwise operator on carrierphase std &15.
 * - Only decode_rawx loggs the data.
 * - 99  for undetermined elevation.
 * - 999 for undetermined azimuth.
 * - timestamp GPS TIME, week , tow and leapseconds
 * - Last version outputs 60 seconds, when the max value is 59... 
 * 
 * Updates 24th Agu 2023:
 * - Added lock time to raw data
 * - Added Dilution of Position dDOP to the pos file
 * - Added the number of satellites used in the position svnum to the pos file.
 * - lat, long format have been scaled by 10e-7.
 * 
 * 
 */
 
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>

#include <errno.h>
#include <fcntl.h>
#include <termios.h>
#include <unistd.h>

#define MAXRAWLEN   8192                /* max length of receiver raw message */
#define MAXOBS      128                 /* ORIGINALLY 64 10/31/2020 max number of obs in an epoch due to max channels */
#define CPSTD_VALID 5
#define UBXSYNC1    0xB5        /* ubx message sync code 1 */
#define UBXSYNC2    0x62        /* ubx message sync code 2 */

#define ID_NAVPVT   0x0107      /* ubx message id: nav solution info */
#define ID_NAVSAT   0x0135      /* ubx message id: satellite information */
#define ID_RXMRAWX  0x0215      /* ubx message id: multi-gnss raw meas data */

#define SYS_NONE    0x00                /* navigation system: none */
#define SYS_GPS     0x01                /* navigation system: GPS */
#define SYS_SBS     0x02                /* navigation system: SBAS */
#define SYS_GLO     0x04                /* navigation system: GLONASS */
#define SYS_GAL     0x08                /* navigation system: Galileo */
#define SYS_QZS     0x10                /* navigation system: QZSS */
#define SYS_CMP     0x20                /* navigation system: BeiDou */
#define SYS_IRN     0x40                /* navigation system: IRNSS */
#define SYS_LEO     0x80                /* navigation system: LEO */
#define SYS_ALL     0xFF                /* navigation system: all */

#define NFREQ       2                   /* number of carrier frequencies changed from 3 to 2 on jul 10, 2022*/
#define NEXOBS      0                   /* number of extended obs codes */

#define CODE_NONE   0
#define CODE_L1C    1
#define CODE_L1B    11
#define CODE_L1X    12
#define CODE_L2C    14
#define CODE_L2S    16
#define CODE_L2L    17
#define CODE_L7I    27
#define CODE_L7Q    28
#define CODE_L2I    40
#define CODE_L1I    47

const static double gpst0[]={1980,1, 6,0,0,0}; /* gps time reference */
const static double gst0 []={1999,8,22,0,0,0}; /* galileo system time reference */
const static double bdt0 []={2006,1, 1,0,0,0}; /* beidou time reference */

#define MINPRNGPS   1                   /* min satellite PRN number of GPS */
#define MAXPRNGPS   32                  /* max satellite PRN number of GPS */
#define NSATGPS     (MAXPRNGPS-MINPRNGPS+1) /* number of GPS satellites */
#define NSYSGPS     1

#define MINPRNGLO   1                   /* min satellite slot number of GLONASS */
#define MAXPRNGLO   32                  /* max satellite slot number of GLONASS */
#define NSATGLO     (MAXPRNGLO-MINPRNGLO+1) /* number of GLONASS satellites */
#define NSYSGLO     1

#define MINPRNGAL   1                   /* min satellite PRN number of Galileo */
#define MAXPRNGAL   36                  /* max satellite PRN number of Galileo */
#define NSATGAL    (MAXPRNGAL-MINPRNGAL+1) /* number of Galileo satellites */
#define NSYSGAL     1

#define MINPRNQZS   193                 /* min satellite PRN number of QZSS */
#define MAXPRNQZS   202                 /* max satellite PRN number of QZSS */
#define MINPRNQZS_S 183                 /* min satellite PRN number of QZSS SAIF */
#define MAXPRNQZS_S 189                 /* max satellite PRN number of QZSS SAIF */
#define NSATQZS     (MAXPRNQZS-MINPRNQZS+1) /* number of QZSS satellites */
#define NSYSQZS     1

#define MINPRNCMP   1                   /* min satellite sat number of BeiDou */
#define MAXPRNCMP   37                  /* max satellite sat number of BeiDou */
#define NSATCMP     (MAXPRNCMP-MINPRNCMP+1) /* number of BeiDou satellites */
#define NSYSCMP     1

#define MINPRNLEO   1                   /* min satellite sat number of LEO */
#define MAXPRNLEO   10                  /* max satellite sat number of LEO */
#define NSATLEO     (MAXPRNLEO-MINPRNLEO+1) /* number of LEO satellites */
#define NSYSLEO     1

#define NSYS        (NSYSGPS+NSYSGLO+NSYSGAL+NSYSQZS+NSYSCMP+NSYSLEO) /* number of systems */
#define MINPRNSBS   120                 /* min satellite PRN number of SBAS */
#define MAXPRNSBS   158                 /* max satellite PRN number of SBAS */
#define NSATSBS     (MAXPRNSBS-MINPRNSBS+1) /* number of SBAS satellites */

#define P2_10       0.0009765625 /* 2^-10 */
#define P2_32       2.328306436538696E-10 /* 2^-32 */

/* get fields (little-endian) ------------------------------------------------*/
#define U1(p) (*((unsigned char *)(p)))
#define I1(p) (*((signed char *)(p)))


static unsigned short U2(unsigned char *p) {unsigned short u; memcpy(&u,p,2); return u;}
static unsigned int   U4(unsigned char *p) {unsigned int   u; memcpy(&u,p,4); return u;}
static int            I2(unsigned char *p) {signed int     u; memcpy(&u,p,2); return u;}
static int            I4(unsigned char *p) {int            u; memcpy(&u,p,4); return u;}
static float          R4(unsigned char *p) {float          r; memcpy(&r,p,4); return r;}
static double         R8(unsigned char *p) {double         r; memcpy(&r,p,8); return r;}
static double         I8(unsigned char *p) {return I4(p+4)*4294967296.0+U4(p);}


#define ROUND(x)    (int)floor((x)+0.5)

typedef char * string;
string s;
string *d;

typedef struct {        /* time struct */
    time_t time;        /* time (s) expressed by standard time_t */
    double sec;         /* fraction of second under 1 s */
} gtime_t;

/*Floats and Doubles
A float can store values from:
-340282346638528859811704183484516925440.0000000000000000 Float lowest
340282346638528859811704183484516925440.0000000000000000 Float max
 * */
#define MAX_SATS 128
#define INTERVAL 60  // 1-minute interval in GPS seconds

#ifndef RT_LOG_DIR
#define RT_LOG_DIR "/home/pi/scintpi/RT"
#endif

typedef struct {
    int prn;
    int constellation;

    double snr_sum[NFREQ];
    double snr_sq_sum[NFREQ];
    int count[NFREQ];
    
    /*
     * ScintKit's one-minute product uses the first value in each GPS-minute
     * bin for pseudorange and the first nonzero value for carrier phase.
     * Retain those same representatives here so the realtime row describes
     * the same [tow_min, tow_min + 60) interval as the post-processed row.
     */
    double P_first[NFREQ];
    double L_first[NFREQ];
    unsigned char have_P[NFREQ];
    unsigned char have_L[NFREQ];


    int elev;
    int az;
} SatData;

SatData sat_data[MAX_SATS];
time_t start_time;

static pthread_mutex_t sat_mutex = PTHREAD_MUTEX_INITIALIZER;

static int rt_week = -1;
static double rt_tow_min = -1.0;
static int rt_time_valid = 0;



typedef struct {        /* observation data record      gtime_t time;       /* receiver sampling time (GPST)  */
    //int week;
    float tow;
    //unsigned char leapseconds;  
    unsigned char cons; /* satellite/receiver gnssId */
    unsigned char sat; /* satellite/receiver number */
    unsigned char prn; /* prn number */
    signed char elev;        /* elev 1 deg resolution */
    int az;                  /* azimuth 1 deg resolution */
    unsigned char SNR[NFREQ+NEXOBS]; /* signal strength (0.25 dBHz) */
    unsigned char qualL[NFREQ+NEXOBS]; /* quality of carrier phase measurement */
    unsigned char qualP[NFREQ+NEXOBS]; /* quality of pseudorange measurement */
    double L[NFREQ+NEXOBS]; /* observation data carrier-phase (cycle) */
    double P[NFREQ+NEXOBS]; /* observation data pseudorange (m) */
    int locktime[NFREQ+NEXOBS];
} obsd_t;

typedef struct {
    float flat,flon,fhei;
    int year;
    int dop;
    char pvt_hour,pvt_min,pvt_sec;
    unsigned char month;
    unsigned char day;
    unsigned char numsv;
} position_t;

typedef struct {
    int n;
    position_t *data;
} pos_t;



typedef struct {
    float tow;
    unsigned char leapseconds;
    signed char scintpiid;
    int UNIQUEID;
    unsigned char YEAR[2];
    unsigned char MONTH[2];
    unsigned char DAY[2];
    double LAT[2];
    double LONG[2];
    int week;
} header_t;

typedef struct {
    int n;
    header_t *data;
} hed_t;

typedef struct {             /* observation data record */
    gtime_t time;            /* receiver sampling time (GPST) */
    int daytime;
    int sdaytime;
    unsigned char sat,rcv;   /* satellite/receiver number */
    unsigned char cons;
    unsigned char SNR;       /* signal strength (0.25 dBHz) */
    signed char elev;        /* elev 1 deg resolution */
    int az;                  /* azimuth 1 deg resolution */
} navs_t;

typedef struct {        /* observation data */
    int n,nmax;         /* number of obervation data/allocated */
    obsd_t *data;       /* observation data records */
} obs_t;

typedef struct {        /* navigation data */
    int n;              /* number of navigation data/allocated */
    navs_t *data;       /* navigation data records */
} nav_t;

typedef struct{
    gtime_t time;
    obs_t obs;          /* observation data */
    nav_t nav;          /* navigation data time,elev,az,SNR */
    hed_t hed;
    pos_t pos;
    int nbyte;          /* number of bytes in message buffer */
    int len;            /* message length (bytes) */
    unsigned char buff[MAXRAWLEN]; /* message buffer */
    char msgtype[256];  /* last message type */
    unsigned char last_block; /* last ubx-message received */
    float flat,flon,fhei; /* BUFFER 3 variables */
    int outtype;        /* output message type */
    int year;
    int last_daytime;
    int dop;            /* Dilution of position*/
    char pvt_hour,pvt_min,pvt_sec;
    unsigned char month;
    unsigned char day;
    unsigned char sday; // start day
    unsigned char numsv;    /* number of satellites for position*/
    unsigned char first_block; // start day
    int week;
    unsigned char leapseconds;
    char rxm_flag;
    char last_pvt_sec;
    char pos_flag;
  } raw_t;

char nextcuthour(char currenthour){
    
    if (currenthour >= 0 && currenthour< 4){
       return 4;
       }
    else if (currenthour>= 4 && currenthour< 8){
       return 8;
       }
    else if (currenthour>= 8 && currenthour< 12){
       return 12;
       }
    else if (currenthour>= 12&& currenthour< 16){
       return 16;
       }
    else if (currenthour>= 16&& currenthour< 20){
       return 20;
       }
    else if (currenthour>= 20&& currenthour< 24){
       return 24;
       } // the last case is going to be cutted by the change of date.
    return 0; // error case
}
int set_interface_attribs(int fd, int speed)
{
    struct termios tty;

    if (tcgetattr(fd, &tty) < 0) {
        printf("Error from tcgetattr: %s\n", strerror(errno));
        return -1;
    }

    cfsetospeed(&tty, (speed_t)speed);
    cfsetispeed(&tty, (speed_t)speed);

    tty.c_cflag |= (CLOCAL | CREAD);    /* ignore modem controls */
    tty.c_cflag &= ~CSIZE;
    tty.c_cflag |= CS8;         /* 8-bit characters */
    tty.c_cflag &= ~PARENB;     /* no parity bit */
    tty.c_cflag &= ~CSTOPB;     /* only need 1 stop bit */
    tty.c_cflag &= ~CRTSCTS;    /* no hardware flowcontrol */

    /* setup for non-canonical mode */
    tty.c_iflag &= ~(IGNBRK | BRKINT | PARMRK | ISTRIP | INLCR | IGNCR | ICRNL | IXON);
    tty.c_lflag &= ~(ECHO | ECHONL | ICANON | ISIG | IEXTEN);
    tty.c_oflag &= ~OPOST;

    /* fetch bytes as they become available */
    tty.c_cc[VMIN] = 1;
    tty.c_cc[VTIME] = 1;

    if (tcsetattr(fd, TCSANOW, &tty) != 0) {
        printf("Error from tcsetattr: %s\n", strerror(errno));
        return -1;
    }
    return 0;
}

void set_mincount(int fd, int mcount)
{
    struct termios tty;

    if (tcgetattr(fd, &tty) < 0) {
        printf("Error tcgetattr: %s\n", strerror(errno));
        return;
    }
    tty.c_cc[VMIN] = mcount ? 1 : 0;
    tty.c_cc[VTIME] = 5;        /* half second timer */
    if (tcsetattr(fd, TCSANOW, &tty) < 0)
        printf("Error tcsetattr: %s\n", strerror(errno));
}


/* time to calendar day/time ---------------------------------------------------
* convert gtime_t struct to calendar day/time
* args   : gtime_t t        I   gtime_t struct
*          double *ep       O   day/time {year,month,day,hour,min,sec}
* return : none
* notes  : proper in 1970-2037 or 1970-2099 (64bit time_t)
*-----------------------------------------------------------------------------*/
extern void time2epoch(gtime_t t, double *ep)
{
    const int mday[]={ /* # of days in a month */
        31,28,31,30,31,30,31,31,30,31,30,31,31,28,31,30,31,30,31,31,30,31,30,31,
        31,29,31,30,31,30,31,31,30,31,30,31,31,28,31,30,31,30,31,31,30,31,30,31
    };
    int days,sec,mon,day;

    /* leap year if year%4==0 in 1901-2099 */
    days=(int)(t.time/86400);
    sec=(int)(t.time-(time_t)days*86400);
    for (day=days%1461,mon=0;mon<48;mon++) {
        if (day>=mday[mon]) day-=mday[mon]; else break;
    }
    ep[0]=1970+days/1461*4+mon/12; ep[1]=mon%12+1; ep[2]=day+1;
    ep[3]=sec/3600; ep[4]=sec%3600/60; ep[5]=sec%60+t.sec;
}

/* convert calendar day/time to time -------------------------------------------
* convert calendar day/time to gtime_t struct
* args   : double *ep       I   day/time {year,month,day,hour,min,sec}
* return : gtime_t struct
* notes  : proper in 1970-2037 or 1970-2099 (64bit time_t)
*-----------------------------------------------------------------------------*/
extern gtime_t epoch2time(const double *ep)
{
    const int doy[]={1,32,60,91,121,152,182,213,244,274,305,335};
    gtime_t time={0};
    int days,sec,year=(int)ep[0],mon=(int)ep[1],day=(int)ep[2];

    if (year<1970||2099<year||mon<1||12<mon) return time;

    /* leap year if year%4==0 in 1901-2099 */
    days=(year-1970)*365+(year-1969)/4+doy[mon-1]+day-2+(year%4==0&&mon>=3?1:0);
    sec=(int)floor(ep[5]);
    time.time=(time_t)days*86400+(int)ep[3]*3600+(int)ep[4]*60+sec;
    time.sec=ep[5]-sec;
    return time;
}

/* gps time to time ------------------------------------------------------------
* convert week and tow in gps time to gtime_t struct
* args   : int    week      I   week number in gps time
*          double sec       I   time of week in gps time (s)
* return : gtime_t struct
*-----------------------------------------------------------------------------*/
gtime_t gpst2time(int week, double sec)
{
    gtime_t t=epoch2time(gpst0);

    if (sec<-1E9||1E9<sec) sec=0.0;
    t.time+=86400*7*week+(int)sec;
    t.sec=sec-(int)sec;
    return t;
}
gtime_t gpst2timeUTC(int week, double sec)
{
    gtime_t t=epoch2time(gpst0);

    if (sec<-1E9||1E9<sec) sec=0.0;
    t.time+=86400*7*week+(int)sec-18;
    t.sec=sec-(int)sec;
    return t;
}
/* time to string --------------------------------------------------------------
* convert gtime_t struct to string
* args   : gtime_t t        I   gtime_t struct
*          char   *s        O   string ("yyyy/mm/dd hh:mm:ss.ssss")
*          int    n         I   number of decimals
* return : none
*-----------------------------------------------------------------------------*/
extern void time2str(gtime_t t, char *s, int n)
{
    double ep[6];

    if (n<0) n=0; else if (n>12) n=12;
    if (1.0-t.sec<0.5/pow(10.0,n)) {t.time++; t.sec=0.0;};
    time2epoch(t,ep);
    /*
    
    sprintf(s,"%04.0f/%02.0f/%02.0f %02.0f:%02.0f:%0*.*f",ep[0],ep[1],ep[2],
            ep[3],ep[4],n<=0?2:n+3,n<=0?0:n,ep[5]);
    */
    //16	00	2.70	21	06	08
	sprintf(s,"%02.0f\t%02.0f\t%0*.*f\t%02.0f\t%02.0f\t%02.0f",ep[3],ep[4],n<=0?2:n+3,n<=0?0:n,ep[5],ep[0]-2000.0,ep[1],ep[2]);
}
/* get time string -------------------------------------------------------------
* get time string
* args   : gtime_t t        I   gtime_t struct
*          int    n         I   number of decimals
* return : time string
* notes  : not reentrant, do not use multiple in a function
*-----------------------------------------------------------------------------*/
char *time_str(gtime_t t, int n) //extern char *time_str(gtime_t t, int n)
{
    static char buff[64];
    time2str(t,buff,n);
    return buff;
}
//Checksum 2020
static int checksum(unsigned char *buff, int len)
{
    unsigned char cka=0,ckb=0;
    int i;

    for (i=2;i<len-2;i++) {
        cka+=buff[i];
        ckb+=cka;
    }
    return cka==buff[len-2]&&ckb==buff[len-1];
}
/*  //old checksum
static unsigned char checksum(unsigned char *buff, int len)
{
    unsigned char cs=0;
    int i;

    for (i=4;i<len-3;i++) {
        cs^=buff[i];
    }
    return cs;
}
*/
/* ubx gnss indicator (ref [2] 25) -------------------------------------------*/
static int ubx_sys(int ind)
{
    switch (ind) {
        case 0: return SYS_GPS;
        case 1: return SYS_SBS;
        case 2: return SYS_GAL;
        case 3: return SYS_CMP;
        case 5: return SYS_QZS;
        case 6: return SYS_GLO;
    }
    return 0;
}
/* ubx sigid to signal ([5] Appendix B) --------------------------------------*/
static int ubx_sig(int sys, int sigid)
{
    if (sys == SYS_GPS) {
        if (sigid == 0) return CODE_L1C; /* L1C/A */
        if (sigid == 3) return CODE_L2L; /* L2C(L) */
        if (sigid == 4) return CODE_L2S; /* L2C(M) */
    }
    else if (sys == SYS_GLO) {
        if (sigid == 0) return CODE_L1C; /* G1C/A (GLO L1 OF) */
        if (sigid == 2) return CODE_L2C; /* G2C/A (GLO L2 OF) */
    }
    else if (sys == SYS_GAL) {
        if (sigid == 0) return CODE_L1C; /* E1C */
        if (sigid == 1) return CODE_L1B; /* E1B */
        if (sigid == 5) return CODE_L7I; /* E5bI */
        if (sigid == 6) return CODE_L7Q; /* E5bQ */
    }
    else if (sys == SYS_QZS) {
        if (sigid == 0) return CODE_L1C; /* L1C/A */
        if (sigid == 5) return CODE_L2L; /* L2CL (not specified in [5]) */
    }
    else if (sys == SYS_CMP) {
        if (sigid == 0) return CODE_L2I; /* B1I D1 (rinex 3.03) */
        if (sigid == 1) return CODE_L2I; /* B1I D2 (rinex 3.03) */
        if (sigid == 2) return CODE_L7I; /* B2I D1 */
        if (sigid == 3) return CODE_L7I; /* B2I D2 */
    }
    else if (sys == SYS_SBS) {
        return CODE_L1C; /* L1C/A (not in [5]) */
    }
    return CODE_NONE;
}
/* signal index in obs data --------------------------------------------------*/
static int sig_idx(int sys, int code)
{
    if (sys == SYS_GPS) {
        if (code==CODE_L1C) return 1;
        if (code==CODE_L2L) return 2;
        if (code==CODE_L2S) return 2;
    }
    else if (sys == SYS_GLO) {
        if (code==CODE_L1C) return 1;
        if (code==CODE_L2C) return 2;
    }
    else if (sys == SYS_GAL) {
        if (code==CODE_L1C) return 1;
        if (code==CODE_L1B) return 1;
        if (code==CODE_L7I) return 2; /* E5bI */
        if (code==CODE_L7Q) return 2; /* E5bQ */
    }
    else if (sys == SYS_QZS) {
        if (code==CODE_L1C) return 1;
        if (code==CODE_L2L) return 2;
    }
    else if (sys == SYS_CMP) {
        if (code==CODE_L1I||code==CODE_L2I) return 1;
        if (code==CODE_L7I) return 2;
    }
    else if (sys == SYS_SBS) {
        if (code==CODE_L1C) return 1;
    }
    return 0;
}

int satno(int sys, int prn)
{
    if (prn<=0) return 0;
    switch (sys) {
        case SYS_GPS:
            if (prn<MINPRNGPS||MAXPRNGPS<prn) return 0;
            return prn-MINPRNGPS+1;
        case SYS_GLO:
            if (prn==255) return NSATGPS+prn-MINPRNGLO+1; // ADDed to read 255 GLONASS
            if (prn<MINPRNGLO||MAXPRNGLO<prn) return 0;
            return NSATGPS+prn-MINPRNGLO+1;
        case SYS_GAL:
            if (prn<MINPRNGAL||MAXPRNGAL<prn) return 0;
            return NSATGPS+NSATGLO+prn-MINPRNGAL+1;
        case SYS_QZS:
            if (prn<MINPRNQZS||MAXPRNQZS<prn) return 0;
            return NSATGPS+NSATGLO+NSATGAL+prn-MINPRNQZS+1;
        case SYS_CMP:
            if (prn<MINPRNCMP||MAXPRNCMP<prn) return 0;
            return NSATGPS+NSATGLO+NSATGAL+NSATQZS+prn-MINPRNCMP+1;
        case SYS_LEO:
            if (prn<MINPRNLEO||MAXPRNLEO<prn) return 0;
            return NSATGPS+NSATGLO+NSATGAL+NSATQZS+NSATCMP+prn-MINPRNLEO+1;
        case SYS_SBS:
            if (prn<MINPRNSBS||MAXPRNSBS<prn) return 0;
            return NSATGPS+NSATGLO+NSATGAL+NSATQZS+NSATCMP+NSATLEO+prn-MINPRNSBS+1;
        }
    return 0;
}


/* TARUN EDIT STARTING HERE 
This used to be for my attempt at a TCP stream which i then turned into file writing. Feel free to clean up. Check start_tcp_server 
**---------------------------------------------------------------------------*/
 



void reset_sat_data(void) {
    pthread_mutex_lock(&sat_mutex);
    memset(sat_data, 0, sizeof(sat_data));
    rt_week = -1;
    rt_tow_min = -1.0;
    rt_time_valid = 0;
    pthread_mutex_unlock(&sat_mutex);
    start_time = time(NULL);
}

static void process_rt_obs(int prn, int constellation, int f_idx,
                           unsigned char snr_raw,
                           double P, double L,
                           int elev, int az)
{
    if (f_idx < 0 || f_idx >= NFREQ) return;

    
    double snr_dbhz = (double)snr_raw;
    double snr_linear = pow(10.0, snr_dbhz / 10.0);

    pthread_mutex_lock(&sat_mutex);

    for (int i = 0; i < MAX_SATS; i++) {
        if (sat_data[i].prn == 0 || (sat_data[i].prn == prn && sat_data[i].constellation == constellation)) {
            int new_satellite = sat_data[i].prn == 0;

            if (sat_data[i].prn == 0) {
                sat_data[i].prn = prn;
                sat_data[i].constellation = constellation;
            }

            /* ScintKit converts zero SNR values to NaN before computing S4. */
            if (snr_raw != 0) {
                sat_data[i].snr_sum[f_idx] += snr_linear;
                sat_data[i].snr_sq_sum[f_idx] += snr_linear * snr_linear;
                sat_data[i].count[f_idx]++;
            }

            /* make_1min() retains the first elevation and azimuth. */
            if (new_satellite) {
                sat_data[i].elev = elev;
                sat_data[i].az = az;
            }

            if (!sat_data[i].have_P[f_idx]) {
                sat_data[i].P_first[f_idx] = P;
                sat_data[i].have_P[f_idx] = 1;
            }
            /* ScintKit replaces zero carrier phase with NaN before first(). */
            if (!sat_data[i].have_L[f_idx] && L != 0.0) {
                sat_data[i].L_first[f_idx] = L;
                sat_data[i].have_L[f_idx] = 1;
            }

            break;
        }
    }

    pthread_mutex_unlock(&sat_mutex);
}

static void log_rt_snapshot(const SatData snap[MAX_SATS],
                            int week, double tow_min) {
    int any = 0;

    // If the completed interval had no observations, do not create a file.
    for (int i = 0; i < MAX_SATS; i++) {
        if (snap[i].prn != 0) { any = 1; break; }
    }
    if (!any) return;

    // Disk I/O is done after the completed interval has been detached from
    // sat_data, so the snapshot keeps its original GPS-minute label.
    char filename[256];
    snprintf(filename, sizeof(filename),
             RT_LOG_DIR "/log_w%04d_tow%010.0f.csv", week, tow_min);

    FILE *fp = fopen(filename, "a+");
    if (!fp) {
        perror("Error opening realtime CSV");
        return;
    }
    fseek(fp, 0, SEEK_END);
    long sz = ftell(fp);
    if (sz == 0) {
        fprintf(fp, "week,tow_min,prn,const,elev,az,"
                    "n_f1,s4_f1,P_f1,L_f1,"
                    "n_f2,s4_f2,P_f2,L_f2\n");
    }

    for (int i = 0; i < MAX_SATS; i++) {
        if (snap[i].prn == 0) continue;

        double s4[NFREQ] = {0};
        for (int f = 0; f < NFREQ; f++) {
            if (snap[i].count[f] > 0) {
                double mean = snap[i].snr_sum[f] / snap[i].count[f];
                double var  = (snap[i].snr_sq_sum[f] / snap[i].count[f]) - (mean * mean);
                if (var < 0) var = 0;
                s4[f] = (mean > 0) ? (sqrt(var) / mean) : 0;
            }
        }

        fprintf(fp, "%d,%.0f,%d,%d,%d,%d,%d,%.6f,",
                week, tow_min,
                snap[i].prn, snap[i].constellation, snap[i].elev, snap[i].az,
                snap[i].count[0], s4[0]);

        if (snap[i].have_P[0]) fprintf(fp, "%.17g,", snap[i].P_first[0]);
        else                   fprintf(fp, ",");
        if (snap[i].have_L[0]) fprintf(fp, "%.17g,", snap[i].L_first[0]);
        else                   fprintf(fp, ",");

        fprintf(fp, "%d,%.6f,",
                snap[i].count[1], s4[1]);

        if (snap[i].have_P[1]) fprintf(fp, "%.17g,", snap[i].P_first[1]);
        else                   fprintf(fp, ",");
        if (snap[i].have_L[1]) fprintf(fp, "%.17g\n", snap[i].L_first[1]);
        else                   fprintf(fp, "\n");
    }

    fclose(fp);
}

/*
 * Advance the realtime accumulator using the GPS time carried by RXM-RAWX.
 * This function is called before observations from the new epoch are added.
 * Therefore every sample in snap has tow in:
 *
 *     [tow_min, tow_min + INTERVAL)
 *
 * and the first sample of the next minute can never leak into the old file.
 */
static void roll_rt_interval(int week, double tow_min) {
    SatData snap[MAX_SATS];
    int completed_week = -1;
    double completed_tow_min = -1.0;
    int have_completed_interval = 0;

    pthread_mutex_lock(&sat_mutex);

    if (!rt_time_valid) {
        // Startup: establish the active GPS minute without logging a partial,
        // empty interval.
        rt_week = week;
        rt_tow_min = tow_min;
        rt_time_valid = 1;
    } else if (week != rt_week || tow_min != rt_tow_min) {
        completed_week = rt_week;
        completed_tow_min = rt_tow_min;
        memcpy(snap, sat_data, sizeof(snap));
        memset(sat_data, 0, sizeof(sat_data));

        rt_week = week;
        rt_tow_min = tow_min;
        have_completed_interval = 1;
    }

    pthread_mutex_unlock(&sat_mutex);

    if (have_completed_interval) {
        log_rt_snapshot(snap, completed_week, completed_tow_min);
    }
}




/* TARUN STOP EDITNG HERE



/* free receiver raw data control ----------------------------------------------
* free observation and ephemeris buffer in receiver raw data control struct
* args   : raw_t  *raw   IO     receiver raw data control struct
* return : none
*-----------------------------------------------------------------------------*/

void free_raw(raw_t *raw)
{
    free(raw->obs.data ); raw->obs.data =NULL; raw->obs.n =0;
    free(raw->nav.data ); raw->nav.data =NULL; raw->nav.n =0;
}

void write_raw(raw_t *raw,FILE *f2)
{
  int n;
  fwrite(raw->obs.data,sizeof(obsd_t),raw->obs.n,f2);
}

void write_pvt(raw_t *raw,FILE *f2)
{
  int n;
  raw->pos.data[0].flat =    raw->flat;
  raw->pos.data[0].flon =    raw->flon;
  raw->pos.data[0].fhei =    raw->fhei;
  raw->pos.data[0].pvt_hour= raw->pvt_hour;
  raw->pos.data[0].pvt_min=  raw->pvt_min;
  raw->pos.data[0].pvt_sec=  raw->pvt_sec;
  raw->pos.data[0].day   =   raw->day;
  raw->pos.data[0].month =   raw->month;  
  raw->pos.data[0].year  =   raw->year;
  raw->pos.data[0].dop   =   raw->dop;
  raw->pos.data[0].numsv =   raw->numsv;    
  
  fwrite(raw->pos.data,sizeof(position_t),raw->pos.n,f2);
}

void write_header(raw_t *raw,FILE *f2)
{
    //float tow;
    //unsigned char leapseconds;
    //signed char scintpiid;
    //int UNIQUEID;
    //unsigned char YEAR[2];
    //unsigned char MONTH[2];
    //unsigned char DAY[2];
    //double LAT[2];
    //double LONG[2];
    //int week;
  raw->hed.data[0].scintpiid=27;
  raw->hed.data[0].UNIQUEID=12790;
  raw->hed.data[0].YEAR[0]='2';
  raw->hed.data[0].YEAR[1]='2';
  raw->hed.data[0].MONTH[0]='0';
  raw->hed.data[0].MONTH[1]='8';
  raw->hed.data[0].DAY[0]='8';
  raw->hed.data[0].DAY[1]='0';
  raw->hed.data[0].LAT[0]=32.99999;
  raw->hed.data[0].LONG[0]=69.99999;
  raw->hed.data[0].leapseconds=raw->leapseconds;
  raw->hed.data[0].tow=raw->obs.data[0].tow;
  raw->hed.data[0].week=raw->week;
        
    
  fwrite(raw->hed.data,sizeof(header_t),raw->hed.n,f2);
}


int update_navs(raw_t *raw)
{
  int j,n;
  for (n=0;n<raw->obs.n;n++) { // look in the table for their respective elevation and azimuth
      for (j=0;j<raw->nav.n;j++) {
          if (raw->obs.data[n].sat==raw->nav.data[j].sat)
          {
              raw->obs.data[n].elev=raw->nav.data[j].elev;
              raw->obs.data[n].az=raw->nav.data[j].az;
              break; // find j and break, continue with the next one.
          }else{
              raw->obs.data[n].elev=99;
              raw->obs.data[n].az=999;
            }
      }
  }
  return 0;
}
/* initialize receiver raw data control ----------------------------------------
* initialize receiver raw data control struct and reallocate obsevation and
* epheris buffer
* args   : raw_t  *raw   IO     receiver raw data control struct
* return : status (1:ok,0:memory allocation error)
*-----------------------------------------------------------------------------*/
int init_raw(raw_t *raw)
{
    int i,j,sys;
    gtime_t time0={0};
    //obsd_t data0={{0}};
	obsd_t data0={0};
    navs_t data1={{0}};
    header_t head0={0};
    position_t data3={0};
    raw->time=time0;
    raw->sday=0; //start day
    raw->first_block=1;
    raw->nbyte=raw->len=0;
    raw->outtype=0;
    raw->flat=0.0;
    raw->flon=0.0;
    raw->fhei=0.0;
    raw->msgtype[0]='\0';
    raw->pvt_hour=99;
    raw->pvt_min=99;
    raw->pvt_sec=99;
    raw->last_pvt_sec=99;
    raw->rxm_flag=99;
    raw->pos_flag=1;
    raw->dop=999;
    raw->numsv=99;
           
    for (i=0;i<MAXRAWLEN;i++) raw->buff[i]=0;
    raw->obs.data =NULL; /* We are going to save data on this variable later*/
    if (!(raw->obs.data =(obsd_t *)malloc(sizeof(obsd_t)*MAXOBS))){
        free_raw(raw);
        return 0;
    }
    raw->obs.n =0;
    for (i=0;i<MAXOBS;i++) raw->obs.data[i]=data0;

    raw->nav.data =NULL;
    if (!(raw->nav.data =(navs_t *)malloc(sizeof(navs_t)*MAXOBS))){
        free_raw(raw);
        return 0;
    }
    raw->nav.n =0;
    for (i=0;i<MAXOBS;i++) raw->nav.data[i]=data1;
    
    raw->hed.n = 1;
    if (!(raw->hed.data=(header_t *)malloc(sizeof(header_t)*1))){
        free(raw->hed.data); raw->hed.data=NULL;raw->hed.n=0;
        return 0;
    }
    raw->hed.data[0]=head0;
    
    raw->pos.n = 1;
    if (!(raw->pos.data=(position_t *)malloc(sizeof(position_t)*1))){
        free(raw->pos.data); raw->pos.data=NULL;raw->pos.n=0;
        return 0;
    }
    raw->pos.data[0]=data3;
    return 1;
}

static int decode_navsat(raw_t *raw)
{
    int itow,ftow,week,i,az,nsat,sat,sys,prn,n,j,svid;
    signed char elev;
    int daytime;
    unsigned char snr,conste;
    n=0; // for each satellite
    int hour,minute;
    float seconds;

    unsigned char *p=raw->buff+6;

    if (!raw->obs.n) { // if rawx has not been decode dont read navsat
      //printf("Waiting for UBX-RXM-RAWX message.\n");
      //TODO : esto puede desemparejar una muestra, vale la pena trabajar en esto?
      return -1;
    }

    nsat=U1(p+5); //Number of measurements to follow
    if (raw->len<16+12*nsat) {
		if (raw->outtype){
			printf("ubx rxmrawx length error: len=%d nsat=%d\n",raw->len,nsat);
		}
        return -1;
    }
    if (raw->outtype) {
        sprintf(raw->msgtype,"UBX NAV-SAT (%4d):",raw->len);
        printf("%s\n",raw->msgtype );
    }
    /* This time comes in mili-seconds*/
    itow=U4(p);

    daytime= (itow-18000)%86400000; // in order to compesate the 18 sec that gpstime has ahead
    hour = round(daytime/3600000);
    minute = round((daytime%3600000)/(60*1000));
    seconds = (daytime%3600000)%(60*1000)/1000.0;

    for (i=0,p+=6;i<nsat&&i<MAXOBS;i++,p+=12) {
        // p begins in +6
        if (!(sys=ubx_sys(U1(p+2))) ) {
			if (raw->outtype){
				printf("ubx rxmrawx: system error\n");
			}
            continue;
        }
        svid=U1(p+3)+(sys==SYS_QZS?MINPRNQZS-1:0);
        if (!(sat=satno(sys,svid))) { // TODO: clarify which variable is eachone
            if (sys==SYS_GLO&&svid==255) {
                continue; /* suppress warning for unknown glo satellite */
            }
            if (raw->outtype){
				printf("ubx rxmrawx sat number error: sys=%2d prn=%2d\n",sys,svid);
			}
            continue;
        }
        conste=U1(p+2);
        elev=U1(p+5);
        az  =I2(p+6);
        /* updating satellite az and elev information*/
        raw->nav.data[n].sat=sat;
        raw->nav.data[n].daytime=daytime; //it seems that this time is less precise
        raw->nav.data[n].elev=elev;
        raw->nav.data[n].az=az;
        n++;
    }
    raw->nav.n=n;
    /* Update raw.obs*/
    return 0;
    
}
static int decode_navpvt(raw_t *raw)
{
    unsigned char *p=raw->buff+6;  // payload starts here
    int n;

    raw->year = U2(p+4)-2000;
    raw->month = U1(p+6);
    raw->day= U1(p+7);
    raw->pvt_hour = U1(p+8);
    raw->pvt_min = U1(p+9);
    raw->pvt_sec = U1(p+10);
    raw->numsv   = U1(p+23);
    raw->flon = I4(p+24)/10000000.0;
    raw->flat = I4(p+28)/10000000.0;
    raw->fhei = I4(p+36)/10000000.0;
    raw->dop  = U2(p+76);
    
    if (raw->pvt_sec!=raw->last_pvt_sec)
    {
        raw->last_pvt_sec=raw->pvt_sec;
        //sprintf(raw->msgtype,"UTC second: %4d ",raw->pvt_sec);
        //printf("%s\n",raw->msgtype);
        raw->pos_flag=1;
    
    }
    
    

    return 0; // Only update some variables dont write information
}

static int decode_rxmrawx(raw_t *raw)
{
    gtime_t time;
    double tow,cp1,pr1,tadj=0.0,toff=0.0,freq,tn;
    int i,j,sys,prn,sat,n=0,nmeas,week,tstat,lockt,slip,halfv,halfc,fcn,cpstd,prstd;
    int leapseconds=0;
    int std_slip=0;
    char *q;
    int ver,code,sigid,f,k;
    unsigned char *p=raw->buff+6;
    unsigned char conste;
    int daytime,hour,minute;
    float seconds;
    int elev = -1, az = -1;


    //nmeas is the Number of measurements to follow(satellites' signals)
    nmeas=U1(p+11); 
    ver  =U1(p+13); /* version ([5] 5.15.3.1) */
    // Each binary package has a head of 24 bytes and 32 bytes for each signal.
    if (raw->len<24+32*nmeas) {
		if (raw->outtype){
			printf("ubx rxmrawx length error: len=%d nsat=%d\n",raw->len,nmeas);
		}
        return -1;
    }
    
    // Week Time
    tow=R8(p);
    //GPS week number in receiver local time
    week=U2(p+8);

    if (week==0) {
        if (raw->outtype){
            printf("ubx rxmrawx week=0 error: len=%d nsat=%d\n",raw->len,nmeas);
        }
        return 0;
    }
    
    double tow_min = floor(tow / (double)INTERVAL) * INTERVAL;

    /*
     * Close the preceding GPS-minute bin before adding any observations from
     * this RXM-RAWX epoch. This makes the receiver timestamp, not wall-clock
     * thread scheduling, authoritative for the 60-second S4 interval.
     */
    roll_rt_interval(week, tow_min);
    leapseconds = I1(p+10);
    time=gpst2timeUTC(week,tow);
    if (raw->outtype) {
        sprintf(raw->msgtype,"UBX RXM-RAWX  (%4d): time=%s nsat=%d",raw->len,
                time_str(time,2),U1(p+11));
        printf("%s\n", raw->msgtype);
    }

    for (i=0,p+=16;i<nmeas&&n<MAXOBS;i++,p+=32) {
        // parameters begin in p+16
        if (!(sys=ubx_sys(U1(p+20))) ) {
			if (raw->outtype){
				printf("ubx rxmrawx: system error\n");
			}
            continue;
        }
        prn=U1(p+21)+(sys==SYS_QZS?MINPRNQZS-1:0);
        if (!(sat=satno(sys,prn))) {
            if (sys==SYS_GLO&&prn==255) {
                continue; /* suppress warning for unknown glo satellite */
            }
            if (raw->outtype){
				printf("ubx rxmrawx sat number error: sys=%2d prn=%2d\n",sys,prn);
			}
            continue;
        }
        pr1=R8(p  );        /* pseudorange*/
        cp1=R8(p+8);        /* carrier-phase*/
        conste=U1(p+20);    /* GNSS ID */
        sigid=U1(p+22);     /* signal ID */
        prstd=U1(p+27);     /* pseudorange std-dev, REMOVED BITWISE OPERATOR &15 */
        cpstd=U1(p+28);     /* carrier-phase std-dev */
        tstat=U1(p+30);     /* tracking status (p+30+16init)*/
        lockt=U2(p+24);     /* lock time usually 64500 ms*/

        prstd=1<<(prstd>=5?prstd-5:0); /* prstd=2^(x-5) */

        if (ver>=1) {
            code=ubx_sig(sys,sigid);
            }
        else {
            code=(sys==SYS_CMP)?CODE_L2I:((sys==SYS_GAL)?CODE_L1X:CODE_L1C);
        }
        /* signal index in obs data */
        f=sig_idx(sys,code);// return frequency index
        /* This only works after the first signal pass*/
        
        raw->week = week;
        raw->leapseconds = leapseconds;
        
        
        for (j=0;j<n;j++) {
			/* search with index j has the data from the readed satellite's signal*/
            if (raw->obs.data[j].sat==sat)
            { break;
            }
        }

        if (j==n) { 
            //raw->obs.data[n].time=time;
            //raw->obs.data[n].week=week;
            //raw->obs.data[n].leapseconds=leapseconds;
            raw->obs.data[n].tow=tow;
            raw->obs.data[n].cons=conste;
            raw->obs.data[n].sat=sat;
            raw->obs.data[n].prn=prn;
            for (k=0;k<NFREQ+NEXOBS;k++) {
                raw->obs.data[n].L[k]=raw->obs.data[n].P[k]=0.0;
                raw->obs.data[n].SNR[k]=0;
                raw->obs.data[n].locktime[k]=0;
            }
            n++;// each new satellite makes n++
        }
        raw->obs.data[j].P[f-1]=pr1;
        raw->obs.data[j].L[f-1]=cp1;
        raw->obs.data[j].qualP[f-1]=prstd;
        raw->obs.data[j].qualL[f-1]=cpstd;
        raw->obs.data[j].SNR[f-1]=U1(p+26);

        for (int nav_idx = 0; nav_idx < raw->nav.n; nav_idx++) {
    if (raw->nav.data[nav_idx].sat == sat) {
        elev = raw->nav.data[nav_idx].elev;
        az = raw->nav.data[nav_idx].az;
        break;
    }
}
       if (f >= 1 && f <= NFREQ) {
    process_rt_obs(prn, conste, f-1,
                   raw->obs.data[j].SNR[f-1],
                   raw->obs.data[j].P[f-1],
                   raw->obs.data[j].L[f-1],
                   elev, az);
}


        raw->obs.data[j].locktime[f-1]=lockt;
    }// FOR's end braket
    raw->time=time;
    raw->obs.n=n;
    
    raw->rxm_flag=0; //allows be sure that rxm data has been readed
    
	return 1;
}

static int decode_ubx(raw_t *raw)
{
    int type=(U1(raw->buff+2)<<8)+U1(raw->buff+3);
    /* checksum function updated*/
    if (!checksum(raw->buff,raw->len)) {
        return -1;
    }  
    switch (type) {
        case ID_RXMRAWX : return decode_rxmrawx(raw);
        case ID_NAVPVT  : return decode_navpvt(raw);
        case ID_NAVSAT  : return decode_navsat(raw);
    }
    if (raw->outtype) {
        sprintf(raw->msgtype,"UBX 0x%02X 0x%02X (%4d)",type>>8,type&0xFF,
                raw->len);
        printf("%s\n",raw->msgtype);
    }
    return 0;
}


/* input ublox raw message from file -------------------------------------------
* fetch next ublox raw data and input a message from file
* args   : raw_t  *raw   IO     receiver raw data control struct
*          FILE   *fp    I      file pointer
* return : status(-2: end of file, -1...9: same as above)
*-----------------------------------------------------------------------------*/
/* sync code -----------------------------------------------------------------*/
static int sync_ubx(unsigned char *buff, unsigned char data)
{
    buff[0]=buff[1];
    buff[1]=data;
    return buff[0]==UBXSYNC1&&buff[1]==UBXSYNC2;
}
int input_ubx(raw_t *raw, unsigned char data)
{
        /* synchronize frame */
    if (raw->nbyte==0) {
        if (!sync_ubx(raw->buff,data)) return 0;
        raw->nbyte=2;
        return 0;
    }
    raw->buff[raw->nbyte++]=data;

    if (raw->nbyte==6) {
        if ((raw->len=U2(raw->buff+4)+8)>MAXRAWLEN) {
            raw->nbyte=0;
            return -1;
        }
    }
    if (raw->nbyte<6||raw->nbyte<raw->len) return 0;
    raw->nbyte=0;

    /* decode ublox raw message */
    return decode_ubx(raw);
}

int input_ubxf(raw_t *raw, FILE *fp)
{
    int i,data;
    /* synchronize frame the for loop for the ub message until find it*/
    // Solo la primera vez entra aqui
    if (raw->nbyte==0)
    {
        for (i=0;;i++) {
            if ((data=fgetc(fp))==EOF){return -2; }
            if (sync_ubx(raw->buff,(unsigned char)data)){break;}
            if (i>=4096) return 0;
        }
    }
    if (fread(raw->buff+2,1,4,fp)<4) return -2;
    raw->nbyte=6;

    if ((raw->len=U2(raw->buff+4)+8)>MAXRAWLEN)
    {
        if (raw->outtype){
			printf("%s\n","Overflow MAXRAWLEN" );
		}
        raw->nbyte=0; // Se regresa a cero el nbyte
        return -1;
    }
    if (fread(raw->buff+6,1,raw->len-6,fp)<(size_t)(raw->len-6)) return -2;

    raw->nbyte=0;
    return decode_ubx(raw); //TODO lo que sigue es ver los mensajes y ver si entra a TRK-MEAS
}


int main(void)
{
    int realtime_capture_started = 0;

    reset_sat_data();

    for (;;) {
        const char *portname = "/dev/ttyACM0";
        //const char *portname = "/dev/ttyACM1";
        //const char *portname = "/dev/ttyUSB0";
        int fd;                 /* Variable to rename the port*/
        char W_OR_E;            /* W or East for filename*/
        char N_OR_S;            /* North or South for filename*/
        char curr_pvt_hour;     /* current hour */
        char next_cut_hour;     /* next cut hour to cut*/
        raw_t raw;              /* Volatile memory to grab the data from the serial port*/
        init_raw(&raw);

        fd = open(portname, O_RDWR | O_NOCTTY | O_SYNC);
        if (fd < 0) {
            printf("Error opening %s: %s\n", portname, strerror(errno));
            return -1;
        }
        /*baudrate 230400, 8 bits, no parity, 1 stop bit */
        set_interface_attribs(fd, B230400); /*Testing */
        set_mincount(fd, 0);                /* set to pure timed read */

        /* First While loop to setup the date and time*/
        while(((&raw)->pvt_hour ==99&&
               (&raw)->pvt_min  ==99&&
               (&raw)->pvt_sec  ==99)||
              (&raw)->rxm_flag ==99)
        {
              unsigned char buf[1000];
              int rdlen;
              rdlen = read(fd, buf, sizeof(buf) - 1);

              if (rdlen > 0) {
                  unsigned char *p;
                  buf[rdlen] = 0; // Write last element with 0
                  for (p = buf; rdlen-- > 0; p++) input_ubx(&raw,*p);
              } else if (rdlen < 0) {
                    printf("Error from read: %d: %s\n", rdlen, strerror(errno));
              } else {  /* rdlen == 0 */
                    printf("try:  dmesg | grep 'tty' \n Or check baudrate.\n ");
              }
        }

        sprintf((&raw)->msgtype,"sudo date -s \"%02d/%02d/20%02d %02d:%02d:%02d\" ",(&raw)->month,(&raw)->day,(&raw)->year,(&raw)->pvt_hour,(&raw)->pvt_min,(&raw)->pvt_sec);
        printf("ScintPi3 Firmware v3.2.6f AGU 24th - 2023.\n");
        printf("Setting hour: %s \n",(&raw)->msgtype);
        system((&raw)->msgtype);
        if ((&raw)->flon<0) {
          W_OR_E = 'W';
        }else{
          W_OR_E = 'E';
        }
        if ((&raw)->flat<0) {
          N_OR_S = 'S';
        }else{
          N_OR_S = 'N';
        }
        sprintf((&raw)->msgtype,"/home/pi/scintpi/raw_data/scintpi3_20%02d%02d%02d_%02d%02d_%.5f%c_%.5f%c_v326f.bin",(&raw)->year,(&raw)->month,(&raw)->day,(&raw)->pvt_hour,(&raw)->pvt_min,fabs((&raw)->flon),W_OR_E,fabs((&raw)->flat),N_OR_S);
        printf("Writing file: %s \n",(&raw)->msgtype);

        (&raw)->sday = (&raw)->day;
        curr_pvt_hour = (&raw)->pvt_hour;
        next_cut_hour = nextcuthour(curr_pvt_hour);


        FILE *fout;
        fout = fopen((&raw)->msgtype, "wb");
        if (fout==NULL)
        {
            perror("Error with filename");
            close(fd);
            return -1;
        }


        sprintf((&raw)->msgtype,"/home/pi/scintpi/raw_data/scintpi3_20%02d%02d%02d_%02d%02d_%.5f%c_%.5f%c_v326f.pos",(&raw)->year,(&raw)->month,(&raw)->day,(&raw)->pvt_hour,(&raw)->pvt_min,fabs((&raw)->flon),W_OR_E,fabs((&raw)->flat),N_OR_S);
        FILE *fpos;
        fpos = fopen((&raw)->msgtype, "wb");
        if (fpos==NULL)
        {
            perror("Error with filename");
            fclose(fout);
            close(fd);
            return -1;
        }

        /* Second While loop to grab the data and write on file
         * make the break at the end of the day and every X hour*/

        unsigned char buf[1000];
        int rdlen;

        /*
         * RXM-RAWX messages received during the initial time/position setup
         * were not written to the high-rate file. Discard only that startup
         * accumulator so realtime and post-processing begin with the same
         * first decoded observation. Keep the accumulator across later
         * four-hour file rotations so a GPS-minute bin is never split.
         */
        if (!realtime_capture_started) {
            reset_sat_data();
            realtime_capture_started = 1;
        }

        write_header(&raw,fout);
        while ((&raw)->sday ==(&raw)->day&&
              (&raw)->pvt_hour<next_cut_hour) {
            rdlen = read(fd, buf, sizeof(buf) - 1);

            if (rdlen > 0) {
                unsigned char *p;
                buf[rdlen] = 0; // Write last element with 0
                // printf("Read %d: \"%s \n", rdlen, buf);
                for (p = buf; rdlen-- > 0; p++)
                {
                  if(input_ubx(&raw,*p))
                  {
                      update_navs(&raw);
                      write_raw(&raw,fout);
                  }
                }
                if ((&raw)->pos_flag)
                {
                    write_pvt(&raw,fpos);
                    (&raw)->pos_flag = 0;
                }
            } else if (rdlen < 0) {
                printf("Error from read: %d: %s\n", rdlen, strerror(errno));

            } else {  /* rdlen == 0 */
                printf("Timeout from read\n");
            }
        }

        fclose(fout);
        fclose(fpos);
        close(fd);
    }
}
