use crate::error::Result;
use crate::runtime::Runtime;

pub async fn run(rt: &Runtime) -> Result<()> {
    let client = rt.api_client()?;
    let me = client.whoami().await?;
    let identity = me
        .email
        .as_deref()
        .or(me.name.as_deref())
        .unwrap_or("unknown");
    let role = me.role.as_deref().unwrap_or("?");
    println!("{identity} ({role}) — {}", rt.server_url()?);
    if let Some(v) = me.server_version {
        println!("server-version: {v}");
    }
    Ok(())
}
