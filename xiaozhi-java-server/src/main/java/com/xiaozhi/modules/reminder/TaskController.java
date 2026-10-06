package com.xiaozhi.modules.reminder;

import com.xiaozhi.common.result.Result;
import com.xiaozhi.modules.security.config.CurrentUser;
import com.xiaozhi.modules.user.entity.UserEntity;
import org.springframework.web.bind.annotation.*;
import reactor.core.publisher.Mono;

/** Old clients receive an explicit retirement notice without accessing archived data. */
@RestController
@RequestMapping("/xiaozhi/api/tasks")
public class TaskController {
    @RequestMapping(value={"", "/**"}, method={RequestMethod.GET,RequestMethod.POST,RequestMethod.PUT,RequestMethod.DELETE,RequestMethod.PATCH})
    public Mono<Result<Void>> retired(@CurrentUser UserEntity user) {
        return Mono.just(user==null?Result.error(401,"未登录"):Result.error(410,"待办功能已移除，请使用日程提醒"));
    }
}
