import unittest
import pandas as pd
from pandas.testing import assert_frame_equal
from replay_display import replay_display_data, replay_status

class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.latest = pd.Timestamp('2026-10-26T14:01:42')
        self.source = pd.DataFrame({'datetime':pd.date_range(end=self.latest, periods=1441, freq='min'), 's4_f1':range(1441), 'TEC':range(1441)})
        self.now = pd.Timestamp('2026-10-28T21:07:00Z')

    def test_rotates_last_day_preserving_observations_and_source(self):
        before = self.source.copy(deep=True)
        result = replay_display_data(self.source, self.now, True)
        assert_frame_equal(self.source, before)
        self.assertEqual(len(result),1440)
        self.assertTrue(result['datetime'].between(self.now.tz_localize(None)-pd.Timedelta('24h'),self.now.tz_localize(None)).all())
        self.assertEqual(set(result.s4_f1),set(range(1,1441)))
        self.assertTrue(result.TEC.eq(result.s4_f1).all())
        self.assertTrue(result.attrs['replay_active'])
        self.assertNotIn('dates shifted',replay_status(result,'old status'))
        self.assertIn('Replay',replay_status(result,'old status'))

    def test_repeats_daily_and_advances_through_archive(self):
        first=replay_display_data(self.source,self.now,True)
        second=replay_display_data(self.source,self.now+pd.Timedelta('24h'),True)
        self.assertEqual(first.s4_f1.tolist(),second.s4_f1.tolist())
        self.assertTrue((second.datetime.reset_index(drop=True)-first.datetime.reset_index(drop=True)).eq(pd.Timedelta('24h')).all())
        advanced=replay_display_data(self.source,self.now+pd.Timedelta('5min'),True)
        self.assertNotEqual(first.s4_f1.tolist(),advanced.s4_f1.tolist())

    def test_fresh_observations_automatically_stop_replay(self):
        fresh=pd.concat([self.source,pd.DataFrame({'datetime':[self.now.tz_localize(None)-pd.Timedelta('2min')],'s4_f1':[0.2],'TEC':[10]})],ignore_index=True)
        result=replay_display_data(fresh,self.now,True)
        assert_frame_equal(result,fresh)
        self.assertFalse(result.attrs['replay_active'])
        self.assertEqual(replay_status(result,'live status'),'live status')

    def test_disabled_or_empty_is_unchanged(self):
        assert_frame_equal(replay_display_data(self.source,self.now,False),self.source)
        self.assertTrue(replay_display_data(self.source.iloc[:0],self.now,True).empty)

if __name__=='__main__':unittest.main()
