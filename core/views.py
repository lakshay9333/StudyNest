import json
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_GET
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.db.models import Q
from django.utils import timezone

from .models import User, FriendRequest, Message, Task, FocusSession


# ─── HELPERS ──────────────────────────────────────────────────────────────────
def json_body(request):
    try:
        return json.loads(request.body)
    except Exception:
        return {}


def ok(data=None):
    return JsonResponse({'ok': True, **(data or {})})


def err(msg, status=400):
    return JsonResponse({'ok': False, 'error': msg}, status=status)


COLORS = ['#c4512a', '#3a6494', '#4a6741', '#7a6494', '#c49a2a', '#6a8070', '#8b4563', '#2a7494']


# ─── PAGES ────────────────────────────────────────────────────────────────────
@ensure_csrf_cookie
def index(request):
    """Single page — shows login or app depending on session."""
    return render(request, 'index.html')


# ─── AUTH API ─────────────────────────────────────────────────────────────────
@csrf_exempt
@require_POST
def api_register(request):
    d = json_body(request)
    name     = (d.get('name') or '').strip()
    username = (d.get('username') or '').strip().lower()
    email    = (d.get('email') or '').strip().lower()
    password = (d.get('password') or '').strip()

    if not name or not username or not password or not email:
        return err('All fields are required.')
    if len(password) < 4:
        return err('Password must be at least 4 characters.')
    if User.objects.filter(username=username).exists():
        return err('Username already taken.')
    if User.objects.filter(email=email).exists():
        return err('This email is already registered. Please log in or use a different email.')

    # pick colour based on user count
    count = User.objects.count()
    color = COLORS[count % len(COLORS)]

    parts = name.split(' ', 1)
    user = User(
        username   = username,
        email      = email,
        first_name = parts[0],
        last_name  = parts[1] if len(parts) > 1 else '',
        color      = color,
    )
    user.set_password(password)
    user.save()  # student_id is auto-generated in save()

    login(request, user)
    return ok({'user': _user_dict(user)})


@csrf_exempt
@require_POST
def api_login(request):
    d = json_body(request)
    identifier = (d.get('username') or '').strip()
    password   = (d.get('password') or '').strip()

    # try username first, then student_id
    user = authenticate(request, username=identifier, password=password)
    if not user:
        try:
            u = User.objects.get(student_id=identifier)
            user = authenticate(request, username=u.username, password=password)
        except User.DoesNotExist:
            pass

    if not user:
        return err('Wrong username or password.')

    login(request, user)
    return ok({'user': _user_dict(user)})


@require_POST
def api_logout(request):
    logout(request)
    return ok()


def api_me(request):
    if not request.user.is_authenticated:
        return JsonResponse({'ok': False, 'authenticated': False})
    return ok({'user': _user_dict(request.user), 'authenticated': True})


def _user_dict(u):
    return {
        'id':         u.id,
        'student_id': u.student_id,
        'name':       u.get_full_name() or u.username,
        'username':   u.username,
        'color':      u.color,
    }


# ─── TASK API ─────────────────────────────────────────────────────────────────
@login_required
def api_tasks(request):
    if request.method == 'GET':
        tasks = Task.objects.filter(user=request.user).values('id', 'text', 'priority', 'done')
        return ok({'tasks': list(tasks)})

    if request.method == 'POST':
        d = json_body(request)
        text = (d.get('text') or '').strip()
        pri  = d.get('priority', 'm')
        if not text:
            return err('Task text is required.')
        t = Task.objects.create(user=request.user, text=text, priority=pri)
        return ok({'task': t.to_dict()})

    return err('Method not allowed.', 405)


@login_required
def api_task_detail(request, task_id):
    try:
        task = Task.objects.get(id=task_id, user=request.user)
    except Task.DoesNotExist:
        return err('Task not found.', 404)

    if request.method == 'PATCH':
        d = json_body(request)
        if 'done' in d:
            task.done = bool(d['done'])
        if 'text' in d:
            task.text = d['text']
        task.save()
        return ok({'task': task.to_dict()})

    if request.method == 'DELETE':
        task.delete()
        return ok()

    return err('Method not allowed.', 405)


# ─── FOCUS SESSION API ────────────────────────────────────────────────────────
@login_required
@require_POST
def api_session_complete(request):
    d = json_body(request)
    mode = d.get('mode', 'focus')
    mins = int(d.get('duration', 25))
    FocusSession.objects.create(user=request.user, mode=mode, duration_mins=mins)
    # Return today's stats
    today = timezone.now().date()
    sessions_today = FocusSession.objects.filter(user=request.user, completed_at__date=today)
    focus_mins = sum(s.duration_mins for s in sessions_today if s.mode == 'focus')
    return ok({
        'sessions_today': sessions_today.count(),
        'focus_mins':     focus_mins,
    })


# ─── USERNAME CHECK API ──────────────────────────────────────────────────────
def api_check_username(request):
    username = request.GET.get('username', '').strip().lower()
    if not username:
        return ok({'available': False})
    taken = User.objects.filter(username=username).exists()
    return ok({'available': not taken, 'username': username})


# ─── FRIEND / SEARCH API ──────────────────────────────────────────────────────
@login_required
def api_search_users(request):
    q = request.GET.get('q', '').strip().lower()
    if not q:
        return ok({'users': []})
    users = User.objects.filter(
        Q(username__icontains=q) | Q(student_id__icontains=q) |
        Q(first_name__icontains=q) | Q(last_name__icontains=q)
    ).exclude(id=request.user.id)[:10]

    result = []
    for u in users:
        rel = _get_relation(request.user, u)
        result.append({**_user_dict(u), 'relation': rel})
    return ok({'users': result})


def _get_relation(me, other):
    fr = FriendRequest.objects.filter(
        Q(sender=me, receiver=other) | Q(sender=other, receiver=me)
    ).first()
    if not fr:
        return 'none'
    if fr.status == 'accepted':
        return 'friend'
    if fr.status == 'pending' and fr.sender == me:
        return 'pending_sent'
    if fr.status == 'pending' and fr.receiver == me:
        return 'pending_recv'
    return 'none'


def _update_last_seen(user):
    if user.is_authenticated:
        User.objects.filter(id=user.id).update(last_seen=timezone.now())


def _is_online(user):
    if not user.last_seen:
        return False
    return (timezone.now() - user.last_seen).total_seconds() < 15


@login_required
@require_POST
def api_send_request(request):
    d = json_body(request)
    to_id = d.get('to_id')
    try:
        receiver = User.objects.get(id=to_id)
    except User.DoesNotExist:
        return err('User not found.')
    if receiver == request.user:
        return err('Cannot add yourself.')
    # If there is already a pending request from receiver to request.user, accept it!
    reverse_fr = FriendRequest.objects.filter(sender=receiver, receiver=request.user, status='pending').first()
    if reverse_fr:
        reverse_fr.status = 'accepted'
        reverse_fr.save()
        return ok({'created': False, 'accepted': True})

    fr, created = FriendRequest.objects.get_or_create(
        sender=request.user, receiver=receiver,
        defaults={'status': 'pending'}
    )
    if not created and fr.status != 'accepted':
        fr.status = 'pending'
        fr.save()
    return ok({'created': created})


@login_required
@require_POST
def api_respond_request(request):
    d = json_body(request)
    from_id = d.get('from_id')
    action  = d.get('action')  # 'accept' or 'reject'
    try:
        fr = FriendRequest.objects.get(sender_id=from_id, receiver=request.user, status='pending')
    except FriendRequest.DoesNotExist:
        return err('Request not found.')
    if action == 'accept':
        fr.status = 'accepted'
        fr.save()
    elif action == 'reject':
        fr.delete()
    return ok()


@login_required
def api_friends(request):
    _update_last_seen(request.user)
    accepted = FriendRequest.objects.filter(
        Q(sender=request.user) | Q(receiver=request.user),
        status='accepted'
    ).select_related('sender', 'receiver')
    
    # Mark all incoming messages from these friends to me as delivered if they are 'sent'
    for fr in accepted:
        friend = fr.receiver if fr.sender == request.user else fr.sender
        Message.objects.filter(sender=friend, receiver=request.user, status='sent').update(status='delivered')

    friends = []
    for fr in accepted:
        friend = fr.receiver if fr.sender == request.user else fr.sender
        # last message preview
        last = Message.objects.filter(
            Q(sender=request.user, receiver=friend) |
            Q(sender=friend, receiver=request.user)
        ).order_by('-created_at').first()
        
        unread_count = Message.objects.filter(
            sender=friend,
            receiver=request.user,
            status__in=['sent', 'delivered']
        ).count()
        
        friends.append({
            **_user_dict(friend),
            'last_msg': ("[This message was deleted]" if last.is_deleted else last.text[:40]) if last else '',
            'unread_count': unread_count,
            'online': _is_online(friend),
        })
    return ok({'friends': friends})


@login_required
def api_pending_requests(request):
    _update_last_seen(request.user)
    incoming = FriendRequest.objects.filter(
        receiver=request.user, status='pending'
    ).select_related('sender')
    return ok({'requests': [
        {**_user_dict(fr.sender), 'fr_id': fr.id} for fr in incoming
    ]})


# ─── MESSAGES API ─────────────────────────────────────────────────────────────
@login_required
def api_messages(request, peer_id):
    try:
        peer = User.objects.get(id=peer_id)
    except User.DoesNotExist:
        return err('User not found.')

    # Verify they are friends
    if _get_relation(request.user, peer) != 'friend':
        return err('Not friends.', 403)

    if request.method == 'GET':
        _update_last_seen(request.user)
        active = request.GET.get('active') == 'true'
        if active:
            Message.objects.filter(sender=peer, receiver=request.user, status__in=['sent', 'delivered']).update(status='seen')
        else:
            Message.objects.filter(sender=peer, receiver=request.user, status='sent').update(status='delivered')

        # Check if requesting older history
        before_id = request.GET.get('before_id')
        if before_id:
            try:
                ref_msg = Message.objects.get(id=int(before_id))
                msgs = Message.objects.filter(
                    Q(sender=request.user, receiver=peer) |
                    Q(sender=peer, receiver=request.user)
                ).filter(created_at__lt=ref_msg.created_at).order_by('-created_at')[:20]
                msgs = reversed(list(msgs))
                return ok({'messages': [m.to_dict(request.user.id) for m in msgs]})
            except Exception:
                return err('Failed to fetch pagination.')

        # Support polling: since= (timestamp ms) — returns messages newer than that ts
        since_ts = request.GET.get('since')
        msgs = Message.objects.filter(
            Q(sender=request.user, receiver=peer) |
            Q(sender=peer, receiver=request.user)
        )
        if since_ts:
            try:
                ts_ms = int(since_ts)
                if ts_ms > 0:
                    from datetime import datetime, timezone as tz, timedelta
                    dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=tz.utc)
                    recent_limit = datetime.now(tz=tz.utc) - timedelta(minutes=10)
                    msgs = msgs.filter(
                        Q(created_at__gt=dt) |
                        Q(sender=request.user, status__in=['sent', 'delivered']) |
                        Q(sender=request.user, status='seen', created_at__gt=recent_limit)
                    )
            except Exception:
                pass
        else:
            # Initial load: get last 35 messages
            msgs = msgs.order_by('-created_at')[:35]
            msgs = list(msgs)
            msgs.reverse()

        peer_typing = False
        if peer.last_typing_peer_id == request.user.id and peer.last_typing_time:
            if (timezone.now() - peer.last_typing_time).total_seconds() < 5:
                peer_typing = True

        return ok({
            'messages': [m.to_dict(request.user.id) for m in msgs],
            'is_typing': peer_typing,
            'online': _is_online(peer)
        })

    if request.method == 'POST':
        text = ''
        file = None
        file_name = None
        file_type = None
        file_size = None
        reply_to_id = None
        
        if request.content_type.startswith('multipart/form-data'):
            text = request.POST.get('text', '').strip()
            reply_to_id = request.POST.get('reply_to')
            file = request.FILES.get('file')
            if file:
                file_name = file.name
                file_size = file.size
                
                # Size validation: max 10MB
                if file_size > 10 * 1024 * 1024:
                    return err('File size exceeds the 10MB limit.')
                
                # Extension validation
                ext = file_name.split('.')[-1].lower() if '.' in file_name else ''
                allowed_exts = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'mp4', 'mov', 'webm', 'pdf', 'docx', 'xlsx', 'pptx', 'ppt', 'txt']
                if ext not in allowed_exts:
                    return err(f'File type .{ext} is not supported.')
                
                if ext in ['png', 'jpg', 'jpeg', 'gif', 'webp']:
                    file_type = 'image'
                elif ext in ['mp4', 'mov', 'webm']:
                    file_type = 'video'
                elif ext in ['pdf', 'docx', 'xlsx', 'pptx', 'ppt', 'txt']:
                    file_type = 'document'
                else:
                    file_type = 'other'
        else:
            d = json_body(request)
            text = (d.get('text') or '').strip()
            reply_to_id = d.get('reply_to')
            
        if not text and not file:
            return err('Message cannot be empty.')

        reply_to = None
        if reply_to_id:
            try:
                reply_to = Message.objects.get(
                    Q(id=reply_to_id) &
                    (Q(sender=request.user, receiver=peer) | Q(sender=peer, receiver=request.user))
                )
            except (Message.DoesNotExist, ValueError):
                pass
            
        msg = Message.objects.create(
            sender=request.user, 
            receiver=peer, 
            text=text, 
            status='sent',
            file=file,
            file_name=file_name,
            file_type=file_type,
            file_size=file_size,
            reply_to=reply_to
        )
        return ok({'message': msg.to_dict(request.user.id)})

    return err('Method not allowed.', 405)


# ─── STATS API ────────────────────────────────────────────────────────────────
@login_required
def api_stats(request):
    today = timezone.now().date()
    sessions = FocusSession.objects.filter(user=request.user, completed_at__date=today)
    focus_mins  = sum(s.duration_mins for s in sessions if s.mode == 'focus')
    sess_count  = sessions.count()
    tasks_total = Task.objects.filter(user=request.user).count()
    tasks_done  = Task.objects.filter(user=request.user, done=True).count()
    return ok({
        'focus_mins':   focus_mins,
        'sessions':     sess_count,
        'tasks_total':  tasks_total,
        'tasks_done':   tasks_done,
    })


# ─── AI SEARCH PROXY ──────────────────────────────────────────────────────────
import urllib.request
import urllib.error
from django.conf import settings

@login_required
@csrf_exempt
@require_POST
def api_ai_search(request):
    """
    Proxy AI search to Anthropic API from the backend.
    Key is read from settings.ANTHROPIC_API_KEY (set in settings.py).
    This avoids the CORS error that happened when the browser called
    api.anthropic.com directly, AND avoids Windows `export` not working.
    """
    d = json_body(request)
    question = (d.get('question') or '').strip()
    model_id  = (d.get('model_id') or 'gpt4').strip()

    if not question:
        return err('Question is required.')

    personas = {
        'gpt4':   'You are GPT-4o by OpenAI. Answer clearly and helpfully with practical focus.',
        'claude': 'You are Claude by Anthropic. Answer thoughtfully with nuance and educational depth.',
        'gemini': 'You are Gemini Pro by Google. Answer comprehensively using broad knowledge.',
    }
    persona = personas.get(model_id, personas['claude'])

    # Read key from settings.py — works on all platforms including Windows
    api_key = getattr(settings, 'ANTHROPIC_API_KEY', '').strip()

    if not api_key or api_key == 'your_api_key_here':
        return ok({
            'answer':          'AI search needs your API key. Open studynest/settings.py and set ANTHROPIC_API_KEY = "your-real-key". Get a free key at https://console.anthropic.com',
            'clarity_score':   7,
            'depth_score':     7,
            'accuracy_score':  7,
            'total':           7.0,
            'key_points':      ['Open studynest/settings.py', 'Set ANTHROPIC_API_KEY = "sk-ant-..."', 'Save the file and restart the server'],
            'model_id':        model_id,
            'fallback':        True,
        })

    system_prompt = (
        f"{persona}\n\n"
        "Respond ONLY with a valid JSON object (no markdown, no backticks, no extra text). Fields:\n"
        "- answer: string (clear 3-5 sentence explanation)\n"
        "- clarity_score: integer 1-10\n"
        "- depth_score: integer 1-10\n"
        "- accuracy_score: integer 1-10\n"
        "- key_points: array of exactly 3 short strings"
    )

    payload = json.dumps({
        'model':      'claude-sonnet-4-20250514',
        'max_tokens': 1000,
        'system':     system_prompt,
        'messages':   [{'role': 'user', 'content': question}],
    }).encode('utf-8')

    req = urllib.request.Request(
        'https://api.anthropic.com/v1/messages',
        data    = payload,
        method  = 'POST',
        headers = {
            'Content-Type':      'application/json',
            'x-api-key':         api_key,
            'anthropic-version': '2023-06-01',
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode('utf-8'))
        raw_text = ''.join(c.get('text', '') for c in body.get('content', []))
        clean    = raw_text.replace('```json', '').replace('```', '').strip()
        parsed   = json.loads(clean)
        total    = round(
            (parsed.get('clarity_score', 5) +
             parsed.get('depth_score',   5) +
             parsed.get('accuracy_score',5)) / 3, 1
        )
        return ok({**parsed, 'total': total, 'model_id': model_id})

    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        return err(f'Anthropic API error {e.code}: {body[:200]}', 502)
    except Exception as e:
        return err(f'AI search failed: {str(e)[:200]}', 500)


@login_required
@require_POST
def api_typing(request, peer_id):
    _update_last_seen(request.user)
    User.objects.filter(id=request.user.id).update(
        last_typing_peer_id=peer_id,
        last_typing_time=timezone.now()
    )
    return ok()


@login_required
def api_message_detail(request, msg_id):
    _update_last_seen(request.user)
    try:
        msg = Message.objects.get(id=msg_id, sender=request.user)
    except Message.DoesNotExist:
        return err('Message not found.', 404)
        
    if request.method == 'PATCH':
        d = json_body(request)
        text = (d.get('text') or '').strip()
        if not text:
            return err('Message text cannot be empty.')
        msg.text = text
        msg.is_edited = True
        msg.save()
        return ok({'message': msg.to_dict(request.user.id)})
        
    if request.method == 'DELETE':
        msg.is_deleted = True
        msg.save()
        return ok({'message': msg.to_dict(request.user.id)})
        
    return err('Method not allowed.', 405)
