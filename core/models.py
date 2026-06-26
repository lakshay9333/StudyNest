from django.db import models
from django.utils import timezone as dj_tz
from django.contrib.auth.models import AbstractUser


# ─── USER ───────────────────────────────────────────────────────────────────
class User(AbstractUser):
    """Extended user — stores student ID, display colour."""
    student_id = models.CharField(max_length=12, unique=True, blank=True)
    color      = models.CharField(max_length=10, default='#4a6741')
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen  = models.DateTimeField(null=True, blank=True)
    last_typing_peer_id = models.IntegerField(null=True, blank=True)
    last_typing_time = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'sn_users'

    def save(self, *args, **kwargs):
        if not self.student_id:
            # Auto-generate SN-XXXX
            last = User.objects.order_by('-id').first()
            n = (last.id + 1) if last else 1
            self.student_id = f'SN-{n:04d}'
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.username} ({self.student_id})'


# ─── FRIEND REQUEST ──────────────────────────────────────────────────────────
class FriendRequest(models.Model):
    STATUS = [('pending', 'Pending'), ('accepted', 'Accepted'), ('rejected', 'Rejected')]
    sender   = models.ForeignKey(User, related_name='sent_requests',     on_delete=models.CASCADE)
    receiver = models.ForeignKey(User, related_name='received_requests', on_delete=models.CASCADE)
    status   = models.CharField(max_length=10, choices=STATUS, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'sn_friend_requests'
        unique_together = ('sender', 'receiver')

    def __str__(self):
        return f'{self.sender} → {self.receiver} [{self.status}]'


# ─── PRIVATE MESSAGE ─────────────────────────────────────────────────────────
class Message(models.Model):
    sender    = models.ForeignKey(User, related_name='sent_messages',     on_delete=models.CASCADE)
    receiver  = models.ForeignKey(User, related_name='received_messages', on_delete=models.CASCADE)
    text      = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    status     = models.CharField(max_length=15, default='sent')

    file       = models.FileField(upload_to='chat_attachments/', null=True, blank=True)
    file_name  = models.CharField(max_length=255, null=True, blank=True)
    file_type  = models.CharField(max_length=50, null=True, blank=True)  # 'image', 'video', 'document', 'other'
    file_size  = models.IntegerField(null=True, blank=True)

    reply_to   = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='replies')
    is_edited  = models.BooleanField(default=False)
    is_deleted = models.BooleanField(default=False)

    class Meta:
        db_table = 'sn_messages'
        ordering = ['created_at']

    def __str__(self):
        return f'{self.sender} → {self.receiver}: {self.text[:40]}'

    def to_dict(self, me_id):
        return {
            'id':              self.id,
            'from':            self.sender_id,
            'to':              self.receiver_id,
            'text':            "[This message was deleted]" if self.is_deleted else self.text,
            'ts':              int(self.created_at.timestamp() * 1000),
            'time':            dj_tz.localtime(self.created_at).strftime('%H:%M'),
            'is_me':           self.sender_id == me_id,
            'status':          self.status,
            'file_url':        None if self.is_deleted else (self.file.url if self.file else None),
            'file_name':       None if self.is_deleted else self.file_name,
            'file_type':       None if self.is_deleted else self.file_type,
            'file_size':       None if self.is_deleted else self.file_size,
            'reply_to':        self.reply_to_id,
            'reply_to_text':   self.reply_to.text if (self.reply_to and self.reply_to.text) else (f"[{self.reply_to.file_type.capitalize()}]" if (self.reply_to and self.reply_to.file_type) else None),
            'reply_to_sender': self.reply_to.sender.first_name or self.reply_to.sender.username if self.reply_to else None,
            'is_edited':       self.is_edited,
            'is_deleted':      self.is_deleted,
        }


# ─── TASK ─────────────────────────────────────────────────────────────────────
class Task(models.Model):
    PRIORITY = [('h', 'High'), ('m', 'Medium'), ('l', 'Low')]
    user       = models.ForeignKey(User, on_delete=models.CASCADE, related_name='tasks')
    text       = models.CharField(max_length=500)
    priority   = models.CharField(max_length=1, choices=PRIORITY, default='m')
    done       = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'sn_tasks'
        ordering = ['-created_at']

    def to_dict(self):
        return {'id': self.id, 'text': self.text, 'pri': self.priority, 'done': self.done}


# ─── FOCUS SESSION ────────────────────────────────────────────────────────────
class FocusSession(models.Model):
    user          = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sessions')
    mode          = models.CharField(max_length=20, default='focus')
    duration_mins = models.IntegerField(default=25)
    completed_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'sn_focus_sessions'
