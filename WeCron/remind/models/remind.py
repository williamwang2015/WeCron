# coding: utf-8
from __future__ import unicode_literals, absolute_import
import logging
import uuid
from urlparse import urljoin
from datetime import timedelta

from tomorrow import threads
from django.db import models
from django.core.urlresolvers import reverse
from django.conf import settings
from django.utils.timezone import localtime, now
from django.contrib.postgres.fields import ArrayField
from django.db.models.signals import pre_save
from django.dispatch import receiver
from common import wechat_client
from remind.utils import nature_time

logger = logging.getLogger(__name__)


class Remind(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)

    time = models.DateTimeField('时间', db_index=True)
    notify_time = models.DateTimeField('提醒时间', db_index=True, null=True)
    defer = models.IntegerField('提前提醒分钟', blank=True, default=0)

    create_time = models.DateTimeField('设置时间', default=now)
    desc = models.TextField('原始描述', default='', blank=True, null=True)
    remark = models.TextField('备注', default='', blank=True, null=True)
    event = models.TextField('提醒事件', default='', blank=True, null=True)
    media_url = models.URLField('语音', max_length=320, blank=True, null=True)
    repeat = models.CharField('重复', max_length=128, blank=True, null=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name='创建者',
                              related_name='time_reminds_created', on_delete=models.DO_NOTHING)
    # participants = models.ManyToManyField('wechat_user.WechatUser', verbose_name='订阅者',
    #                                       related_name='time_reminds_participate')
    participants = ArrayField(models.CharField(max_length=40), verbose_name='订阅者', default=list)
    done = models.NullBooleanField('状态', default=False,
                                        choices=((False, '未发送'), (True, '已发送'),))

    class Meta:
        ordering = ["-time"]
        db_table = 'time_remind'

    def time_until(self):
        """Returns 11小时28分后"""
        return nature_time(self.time)

    def nature_time_defer(self):
        if not self.defer:
            return '准时'
        units = {'周': 7*24*60, '天': 60*24, '小时': 60, '分钟': 1}
        for unit, minutes in units.items():
            if self.defer % minutes == 0:
                return '%s %s %s' %('提前' if self.defer < 0 else '延后',
                                    abs(self.defer/minutes),
                                    unit)

    def local_time_string(self, fmt='%Y/%m/%d %H:%M'):
        return localtime(self.time).strftime(fmt)

    def title(self):
        if self.event:
            return self.event
        return '闹钟'

    @threads(10, timeout=60)
    def notify_user_by_id(self, uid):
        # TODO wechatpy is not thread-safe
        user = self.owner._default_manager.get(pk=uid)
        name = user.get_full_name()
        if not user.subscribe:
            logger.info('User %s has unsubscribed, skip sending notification' % name)
            return
        try:
            res = wechat_client.message.send_template(
                        user_id=uid,
                        template_id='IxUSVxfmI85P3LJciVVcUZk24uK6zNvZXYkeJrCm_48',
                        url=self.get_absolute_url(full=True),
                        top_color='#459ae9',
                        data={
                               "first": {
                                   "value": '\U0001F552 %s\n' % self.title(),
                                   "color": "#459ae9"
                               },
                               "keyword1": {
                                   "value": self.desc,
                               },
                               "keyword2": {
                                   "value": self.local_time_string(),
                               },
                               "remark": {
                                   "value": "提醒时间：" + self.nature_time_defer(),
                               }
                        },
                    )
            logger.info('Successfully send notification to user %s(%s)', name, uid)
            return res
        except:
            logger.exception('Failed to send notification to user %s(%s)', name, uid)

    def notify_users(self):
        for uid in [self.owner_id] + self.participants:
            self.notify_user_by_id(uid)

    def add_participant(self, uid):
        if uid in self.participants:
            return
        self.participants.append(uid)
        self.save(update_fields=['participants'])

    def remove_participant(self, uid):
        if uid not in self.participants:
            return
        self.participants.remove(uid)
        self.save(update_fields=['participants'])

    def subscribed_by(self, user):
        return self.owner_id == user.pk or user.pk in self.participants

    def get_absolute_url(self, full=False):
        url = reverse('remind_update', kwargs={'pk': self.pk.hex})
        if full:
            return urljoin('http://www.weixin.at', url)
        return url

    def __unicode__(self):
        return '%s: %s (%s)' % (self.owner.nickname, self.desc or self.event,
                                self.local_time_string('%Y/%m/%d %H:%M:%S'))


@receiver(pre_save, sender=Remind, dispatch_uid='update-notify-time')
def update_notify_time(instance, **kwargs):
    instance.notify_time = instance.time + timedelta(minutes=instance.defer)
