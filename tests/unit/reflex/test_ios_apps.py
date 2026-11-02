from types import SimpleNamespace

from artemis.reflex.ios_apps import IOSAppLauncher


class FakeControl:
    def __init__(self):
        self.calls = []
    def press_named_key(self, name):
        self.calls.append(('key', name))
    def ios_home(self):
        self.calls.append(('home',))
    def type_text(self, text):
        self.calls.append(('type', text))


class FakeCapture:
    def __init__(self, images):
        self.images = list(images)
    def grab(self):
        return SimpleNamespace(image=self.images.pop(0))


def test_launch_uses_keyboard_spotlight_sequence():
    control = FakeControl()
    launcher = IOSAppLauncher(control, wait_s=0)
    result = launcher.open_app('taobao', query='taobao')

    assert result.ok is True
    assert result.method == 'spotlight'
    assert ('key', 'spotlight') in control.calls
    assert ('type', 'taobao') in control.calls
    assert control.calls[-2:] == [('key', 'down'), ('key', 'enter')]


def test_launch_verifies_screen_change_when_capture_is_given():
    control = FakeControl()
    capture = FakeCapture([b'home', b'app'])
    launcher = IOSAppLauncher(control, wait_s=0)
    result = launcher.open_app('wechat', query='wechat', capture=capture, verify=lambda before, after: True)
    assert result.ok is True


def test_launch_reports_no_screen_change():
    control = FakeControl()
    capture = FakeCapture([b'same', b'same'])
    launcher = IOSAppLauncher(control, wait_s=0)
    result = launcher.open_app('wechat', query='wechat', capture=capture, verify=lambda before, after: False)
    assert result.ok is False
    assert result.error == 'no_screen_change'
